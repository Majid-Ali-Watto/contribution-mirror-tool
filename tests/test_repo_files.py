"""Repository hygiene: the license, the importable ruleset and the documentation links stay valid."""
import glob
import json
import os
import re
import unittest

import _path

ROOT = _path.ROOT


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Files(unittest.TestCase):
    def test_license_is_the_standard_mit_text(self):
        text = read("LICENSE")
        self.assertTrue(text.startswith("MIT License\n"))
        self.assertRegex(text, r"Copyright \(c\) \d{4} \S+")
        self.assertIn('THE SOFTWARE IS PROVIDED "AS IS"', text)

    def test_ruleset_protects_the_default_branch_and_can_be_posted_to_github_as_is(self):
        rs = json.loads(read(".github", "rulesets", "default-branch-protection.json"))
        self.assertEqual((rs["target"], rs["enforcement"], rs["name"]), ("branch", "active", "default-branch-protection"))
        self.assertEqual(rs["conditions"]["ref_name"], {"include": ["~DEFAULT_BRANCH"], "exclude": []})
        self.assertEqual([r["type"] for r in rs["rules"]], ["deletion", "non_fast_forward", "pull_request"])
        pr = rs["rules"][2]["parameters"]
        self.assertEqual(pr["required_approving_review_count"], 1)
        self.assertEqual(rs["bypass_actors"], [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}])
        self.assertNotIn("id", rs)                            # an export with an id/source would be rejected on create

    def test_every_relative_link_in_the_docs_resolves(self):
        broken = []
        for md in ["README.md", "CLAUDE.md", *sorted(glob.glob(os.path.join(ROOT, "docs", "*.md")))]:
            path = md if os.path.isabs(md) else os.path.join(ROOT, md)
            for m in re.finditer(r"\]\(([^)\s]+)\)", read(os.path.relpath(path, ROOT))):
                target = m.group(1).split("#")[0]
                if target and not target.startswith(("http://", "https://", "mailto:")):
                    if not os.path.exists(os.path.normpath(os.path.join(os.path.dirname(path), target))):
                        broken.append((os.path.relpath(path, ROOT), target))
        self.assertEqual(broken, [])

    def test_the_privacy_promise_no_web_address_in_the_code_beyond_the_hosts_it_supports(self):
        # docs/privacy.md says the only addresses written into the code are the supported hosts' public API addresses.
        allowed = {"api.github.com", "api.bitbucket.org", "dev.azure.com", "host"}   # 'host' is the placeholder in a docstring
        found = set()
        for dirpath, _, names in os.walk(os.path.join(ROOT, "src")):
            for n in names:
                if n.endswith(".py"):
                    with open(os.path.join(dirpath, n), encoding="utf-8") as f:
                        found |= set(re.findall(r"https?://([A-Za-z0-9.-]+)", f.read()))
        self.assertEqual(found - allowed, set(), "a new address in the code: update docs/privacy.md before allowing it here")

    def test_privacy_and_terms_exist_are_linked_and_give_a_contact(self):
        readme = read("README.md")
        for page in ("privacy", "terms"):
            self.assertIn(f"docs/{page}.md", readme)
            self.assertIn("mailto:", read("docs", f"{page}.md"))
        for needle in ("majidev.netlify.app", "linkedin.com/in/", "mailto:"):
            self.assertIn(needle, readme)

    def test_no_machine_specific_or_private_text_in_the_published_files(self):
        """Published files must not contain a real home-directory path, nor any name listed in tests/.private-patterns.
        That file (one regex per line, # for comments) is git-ignored on purpose: the names to keep out of a public repo
        must not be written into the repo themselves. Without it only the generic check runs."""
        patterns = [re.compile(r"/home/([A-Za-z0-9._-]+)/"), re.compile(r"/Users/([A-Za-z0-9._-]+)/"),
                    re.compile(r"C:\\Users\\([A-Za-z0-9._-]+)\\")]
        placeholders = {"u", "me", "you", "user", "name", "username"}
        private = []
        path = os.path.join(ROOT, "tests", ".private-patterns")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                private = [re.compile(line.strip(), re.I) for line in f if line.strip() and not line.startswith("#")]
        offenders = []
        skip = {os.path.join("config", ".env"), os.path.join("config", "repos.json"), os.path.join("tests", ".private-patterns")}
        for base in ("src", "tests", "docs", "config", "scripts", ".github"):
            for dirpath, _, names in os.walk(os.path.join(ROOT, base)):
                for n in names:
                    rel = os.path.relpath(os.path.join(dirpath, n), ROOT)
                    if rel in skip or "__pycache__" in rel:
                        continue
                    with open(os.path.join(ROOT, rel), "rb") as f:
                        data = f.read()
                    if b"\0" in data[:8192]:
                        # binary (images): compressed bytes match short patterns by chance, and the text drawn in an
                        # image cannot be found this way anyway; screenshots are checked by eye before they are added
                        continue
                    text = data.decode("utf-8", errors="ignore")
                    if any(m.group(1) not in placeholders for pat in patterns for m in pat.finditer(text)) \
                            or any(p.search(text) for p in private):
                        offenders.append(rel)
        for name in ("README.md", "LICENSE", "CLAUDE.md"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as f:
                text = f.read()
            if any(p.search(text) for p in private) or (name == "CLAUDE.md" and any(
                    m.group(1) not in placeholders for pat in patterns for m in pat.finditer(text))):
                offenders.append(name)
        self.assertEqual(offenders, [])

if __name__ == "__main__":
    unittest.main()

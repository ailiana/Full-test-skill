"""Check the shipped marketplace against its actual plugin and skill files.

Path resolution follows the marketplace-root rules documented at
https://developers.openai.com/plugins/build/plugins (not .agents/plugins/).
These checks do not register a marketplace or modify user configuration.
"""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import re
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / ".agents" / "plugins" / "marketplace.json"


def resolve_contained(root: Path, value: str) -> Path:
    """Resolve a portable ./ path and reject escaped or missing local sources."""
    if (not isinstance(value, str) or not value.startswith("./")
            or "\\" in value or ":" in value or "\x00" in value):
        raise ValueError("Expected a portable ./-prefixed relative path")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Path must remain inside the marketplace root")
    try:
        resolved_root = root.resolve(strict=True)
        resolved = (resolved_root / value).resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError):
        raise ValueError("Local source is missing or escapes the marketplace root") from None
    return resolved


class MarketplaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        cls.entry = cls.catalog["plugins"][0]

    def test_catalog_identifies_the_requested_market_and_available_plugin(self):
        self.assertEqual(self.catalog["name"], "ailiana-plugins")
        self.assertEqual(self.catalog["interface"]["displayName"], "ailiana 插件市场")
        self.assertEqual(len(self.catalog["plugins"]), 1)
        self.assertEqual(self.entry["policy"], {"installation": "AVAILABLE", "authentication": "ON_INSTALL"})
        self.assertEqual(self.entry["category"], "Productivity")

    def test_source_resolves_to_actual_plugin_root_not_catalog_directory(self):
        source = self.entry["source"]
        self.assertEqual(source["source"], "local")
        plugin_root = resolve_contained(ROOT, source["path"])
        self.assertEqual(plugin_root, ROOT.resolve())
        self.assertNotEqual(plugin_root, CATALOG.parent.resolve())
        self.assertTrue((plugin_root / "plugin.json").is_file())
        self.assertTrue((plugin_root / ".codex-plugin" / "plugin.json").is_file())

    def test_catalog_identity_matches_both_packaged_manifests(self):
        plugin_root = resolve_contained(ROOT, self.entry["source"]["path"])
        portable = json.loads((plugin_root / "plugin.json").read_text(encoding="utf-8"))
        compatibility = json.loads((plugin_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(self.entry["name"], portable["name"])
        self.assertEqual(portable["name"], compatibility["name"])
        self.assertEqual(portable["name"], "full-test")
        self.assertRegex(portable["name"], r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
        self.assertEqual(portable["version"], compatibility["version"])
        self.assertRegex(portable["version"], r"^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$")

    def test_both_manifests_discover_the_same_contained_skill(self):
        plugin_root = resolve_contained(ROOT, self.entry["source"]["path"])
        compatibility = json.loads((plugin_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        portable_skills = resolve_contained(plugin_root, "./skills/")
        compatibility_skills = resolve_contained(plugin_root, compatibility["skills"])
        self.assertEqual(compatibility_skills, portable_skills)
        skills = list(portable_skills.glob("*/SKILL.md"))
        self.assertTrue(skills, "Marketplace entry must expose at least one installed skill")
        discovered_names = set()
        for skill in skills:
            resolved = resolve_contained(plugin_root, "./" + skill.relative_to(plugin_root).as_posix())
            self.assertTrue(resolved.is_file())
            text = resolved.read_text(encoding="utf-8")
            front = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
            self.assertIsNotNone(front, "Packaged SKILL.md needs frontmatter")
            fields = dict(line.split(":", 1) for line in front[1].splitlines() if ":" in line)
            name = fields.get("name", "").strip().strip("\"'")
            self.assertEqual(name, skill.parent.name)
            self.assertTrue(fields.get("description", "").strip())
            discovered_names.add(name)
        self.assertIn("full-test", discovered_names)

    def test_portable_source_path_rejects_external_and_missing_locations(self):
        for value in ("../", "./../", "./skills/../../", "/tmp", "C:/outside", "https://example.test/plugin", "skills/", ".\\skills", "./missing-plugin"):
            with self.subTest(path=value), self.assertRaises(ValueError):
                resolve_contained(ROOT, value)

    def test_symlink_cannot_make_a_local_source_escape_its_marketplace(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            market, outside = base / "market", base / "outside"
            market.mkdir()
            outside.mkdir()
            link = market / "plugin-link"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("This host does not permit creating a directory symlink")
            with self.assertRaises(ValueError):
                resolve_contained(market, "./plugin-link")


if __name__ == "__main__":
    unittest.main()

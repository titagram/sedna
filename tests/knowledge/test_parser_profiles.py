"""Behavioral tests for source-specific structural cleanup profiles."""

import json
from pathlib import Path

import pytest

from sedna.knowledge.parsing import BlockKind, parse_markdown
from sedna.knowledge.parsing.profiles import apply_profile

FIXTURES = Path(__file__).parent / "fixtures"


def parse_fixture(name: str):
    markdown = (FIXTURES / name).read_text(encoding="utf-8")
    return parse_markdown(f"fixture-{name}", name, markdown)


def joined_text(document) -> str:
    return "\n".join(block.text for block in document.blocks)


def test_htb_scrape_removes_interface_regions_but_keeps_article_content():
    cleaned = apply_profile(parse_fixture("htb-scrape.md"), "htb_scrape")
    text = joined_text(cleaned)

    assert cleaned.blocks[0].kind is BlockKind.HEADING
    assert cleaned.blocks[0].text == "Virtual Hosts"
    assert "Virtual hosts route requests" in text
    assert "Next Steps for Discovery" in text
    assert "Keep the Pwnbox tunnel" in text
    assert "Dashboard is a valid discovered virtual host" in text
    assert "Section 9 / 19" not in text
    assert "Footer material" not in text
    assert all(block.text != "Previous" for block in cleaned.blocks)
    assert all(block.text != "Next" for block in cleaned.blocks)


def test_htb_scrape_reconciles_assets_and_relationships_after_boundary_cleanup():
    source = parse_markdown(
        "source-htb",
        "htb.md",
        "[Dashboard](https://ui.test)\n\n"
        "# Article\n\n"
        "Keep [reference](https://reference.test) and ![proof](proof.png).\n\n"
        "Previous\n\n"
        "![footer](footer.png)\n",
    )

    cleaned = apply_profile(source, "htb_scrape")

    assert cleaned.relationships == ("https://reference.test",)
    assert [asset.target for asset in cleaned.assets] == ["proof.png"]


def test_htb_scrape_without_an_article_heading_is_loss_preserving():
    source = parse_markdown(
        "source-htb",
        "headingless.md",
        "Dashboard\n\nRun the next validation step.\n",
    )

    assert apply_profile(source, "htb_scrape") == source


def test_obsidian_profile_removes_only_metadata_and_records_note_relationships():
    cleaned = apply_profile(parse_fixture("obsidian-lesson.md"), "academy_obsidian")
    text = joined_text(cleaned)

    assert "Tags:" not in text
    assert "Related to:" not in text
    assert "See also:" not in text
    assert "Previous:" not in text
    assert "Compare Proof screenshot with Second view." in text
    assert "Read the protocol specification before testing." in text
    assert cleaned.relationships == (
        "FTP",
        "DNS",
        "Enumeration",
        "HTB Academy",
        "https://example.test/spec",
    )


def test_obsidian_profile_converts_each_embed_occurrence_without_ocr():
    cleaned = apply_profile(parse_fixture("obsidian-lesson.md"), "academy_obsidian")

    assert [asset.target for asset in cleaned.assets] == [
        "logo_footprinting.png",
        "proof.png",
        "proof.png",
    ]
    assert [asset.alt_text for asset in cleaned.assets] == [
        None,
        "Proof screenshot",
        "Second view",
    ]
    assert [(asset.start_line, asset.end_line) for asset in cleaned.assets] == [
        (8, 8),
        (10, 10),
        (10, 10),
    ]
    assert all(asset.metadata["source"] == "obsidian_embed" for asset in cleaned.assets)


def test_obsidian_profile_keeps_non_metadata_prose_in_same_paragraph():
    source = parse_markdown(
        "source-note",
        "note.md",
        "Tags: #academy\n"
        "Related to: [[DNS]]\n"
        "This procedural line must remain.\n",
    )

    cleaned = apply_profile(source, "academy_obsidian")

    assert joined_text(cleaned) == "This procedural line must remain."
    assert (cleaned.blocks[0].start_line, cleaned.blocks[0].end_line) == (3, 3)


def test_obsidian_profile_collects_body_wikilinks_in_order_but_not_code():
    source = parse_markdown(
        "source-note",
        "graph.md",
        "# Graph\n\n"
        "[[Folder/First Note|First alias]] then [[Second]] then "
        "[[Folder/First Note|Repeated alias]].\n\n"
        "Read [reference](https://reference.test).\n\n"
        "```bash\n"
        'if [[ -f "$candidate" ]]; then\n'
        "  echo [[Not an Obsidian relationship]]\n"
        "fi\n"
        "```\n\n"
        "Continue with [[Third|third alias]].\n",
    )

    cleaned = apply_profile(source, "academy_obsidian")

    assert cleaned.relationships == (
        "Folder/First Note",
        "Second",
        "https://reference.test",
        "Third",
    )


def test_obsidian_profile_excludes_inline_code_wikilink_literals():
    source = parse_markdown(
        "source-note",
        "inline-code.md",
        "Treat `[[Literal Syntax]]` as code, then open [[Actual Note]].\n",
    )

    cleaned = apply_profile(source, "academy_obsidian")

    assert cleaned.relationships == ("Actual Note",)


def test_obsidian_profile_rebases_inline_code_after_metadata_and_embed_rewrites():
    source = parse_markdown(
        "source-note",
        "combined.md",
        "Tags: #academy\n"
        "Related to: [[DNS]]\n"
        "Keep ![[proof.png|Proof]] then `[[Literal Code]]` and "
        "open [[Folder/Actual Note|actual]].\n",
    )

    cleaned = apply_profile(source, "academy_obsidian")
    body = cleaned.blocks[0]
    spans = json.loads(body.metadata["inline_code_spans"])

    assert body.text == (
        "Keep Proof then [[Literal Code]] and open [[Folder/Actual Note|actual]]."
    )
    assert body.text[slice(*spans[0])] == "[[Literal Code]]"
    assert cleaned.relationships == ("DNS", "Folder/Actual Note")
    assert [asset.target for asset in cleaned.assets] == ["proof.png"]
    assert apply_profile(cleaned, "academy_obsidian") == cleaned


def test_obsidian_profile_preserves_urls_on_removed_metadata_lines_not_tags():
    source = parse_markdown(
        "source-note",
        "metadata.md",
        "# Note\n\n"
        "Tags: [[Tag Taxonomy]] #academy\n"
        "Related to: [[DNS|DNS notes]] and [vendor](https://vendor.test)\n"
        'See also: <a href="https://html.test">HTML reference</a>\n'
        "Previous:\n\n"
        "Retained body.\n",
    )

    cleaned = apply_profile(source, "academy_obsidian")

    assert cleaned.relationships == (
        "DNS",
        "https://vendor.test",
        "https://html.test",
    )
    assert "Tag Taxonomy" not in cleaned.relationships


def test_obsidian_profile_discards_tag_line_urls_but_keeps_other_metadata_urls():
    source = parse_markdown(
        "source-note",
        "metadata-urls.md",
        "# Note\n\n"
        "Tags: [taxonomy](https://tags.test) "
        '<a href="https://tag-html.test">tag</a> [[Tag Note]]\n'
        "Related to: [vendor](https://vendor.test)\n"
        'See also: <a href="https://reference.test">reference</a>\n'
        "Previous: [[Academy/Home]]\n\n"
        "Body [guide](https://body.test).\n",
    )

    cleaned = apply_profile(source, "academy_obsidian")

    assert cleaned.relationships == (
        "https://vendor.test",
        "https://reference.test",
        "Academy/Home",
        "https://body.test",
    )
    assert "https://tags.test" not in cleaned.relationships
    assert "https://tag-html.test" not in cleaned.relationships
    assert "Tag Note" not in cleaned.relationships


def test_github_profile_unwraps_centered_presentation_without_losing_content():
    cleaned = apply_profile(parse_fixture("github-walkthrough.md"), "github_walkthrough")

    assert [block.text for block in cleaned.blocks[:3]] == [
        "Lame",
        "Linux · Easy",
        "Machine avatar",
    ]
    assert [block.kind for block in cleaned.blocks[:3]] == [
        BlockKind.HEADING,
        BlockKind.PARAGRAPH,
        BlockKind.IMAGE,
    ]
    assert "align=\"center\"" not in joined_text(cleaned)
    assert "Run the initial scan" in joined_text(cleaned)
    assert "Use the observed Samba version" in joined_text(cleaned)
    assert cleaned.relationships == ("https://example.test/machine",)
    assert [asset.target for asset in cleaned.assets] == ["avatar.png", "scan.png"]


def test_github_profile_preserves_non_centered_html_verbatim():
    source = parse_markdown(
        "source-github",
        "walkthrough.md",
        '<div class="technical-note">Do not unwrap <code>this</code>.</div>\n',
    )

    assert apply_profile(source, "github_walkthrough") == source


def test_github_profile_does_not_rewrite_centered_html_inside_procedural_code():
    source = parse_markdown(
        "source-github",
        "walkthrough.md",
        "```html\n<div align=\"center\">payload text</div>\n```\n",
    )

    assert apply_profile(source, "github_walkthrough") == source


def test_github_profile_preserves_breaks_inside_centered_root_wrapper():
    source = parse_markdown(
        "source-github",
        "walkthrough.md",
        '<div align="center">Host<br>10.10.10.10</div>\n',
    )

    cleaned = apply_profile(source, "github_walkthrough")

    assert cleaned.blocks[0].text == "Host\n10.10.10.10"


def test_github_profile_ignores_centered_descendant_of_technical_outer_wrapper():
    source = parse_markdown(
        "source-github",
        "walkthrough.md",
        '<div class="technical"><div align="center">Title</div>'
        '<code>curl TARGET_IP</code></div>\n',
    )

    assert apply_profile(source, "github_walkthrough") == source


def test_github_profile_keeps_nested_and_sibling_content_inside_centered_root():
    source = parse_markdown(
        "source-github",
        "walkthrough.md",
        '<div align="center"><span>Host</span><br>'
        '<code>curl TARGET_IP</code><p>Observed output</p>'
        '<a href="https://reference.test">Reference</a></div>\n',
    )

    cleaned = apply_profile(source, "github_walkthrough")

    assert cleaned.blocks[0].text == (
        "Host\ncurl TARGET_IP\nObserved output\nReference"
    )
    assert cleaned.relationships == ("https://reference.test",)


def test_github_profile_is_idempotent_when_visible_code_looks_like_centered_html():
    source = parse_markdown(
        "source-github",
        "walkthrough.md",
        '<div align="center"><code>&lt;div align="center"&gt;'
        'x&lt;/div&gt;</code><a href="https://reference.test"></a>'
        '<img src="proof.png" alt="proof"></div>\n',
    )

    once = apply_profile(source, "github_walkthrough")
    twice = apply_profile(once, "github_walkthrough")

    assert once.blocks[0].text == '<div align="center">x</div>'
    assert once.relationships == ("https://reference.test",)
    assert [asset.target for asset in once.assets] == ["proof.png"]
    assert twice == once
    assert twice.model_validate_json(twice.model_dump_json()) == twice


@pytest.mark.parametrize(
    ("fixture", "profile"),
    [
        ("htb-scrape.md", "htb_scrape"),
        ("obsidian-lesson.md", "academy_obsidian"),
        ("github-walkthrough.md", "github_walkthrough"),
    ],
)
def test_profiles_are_idempotent(fixture, profile):
    once = apply_profile(parse_fixture(fixture), profile)

    assert apply_profile(once, profile) == once


def test_unknown_profile_is_rejected_explicitly():
    with pytest.raises(ValueError, match="unsupported parser profile"):
        apply_profile(parse_fixture("github-walkthrough.md"), "unknown")

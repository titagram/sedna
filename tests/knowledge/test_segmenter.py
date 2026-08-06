from __future__ import annotations

import pytest

from sedna.knowledge.parsing.markdown import parse_markdown
from sedna.knowledge.parsing.models import BlockKind, ParsedBlock, ParsedDocument
from sedna.knowledge.parsing.sanitize import sanitize_searchable_text
from sedna.knowledge.parsing.segment import segment_document


def parsed(markdown: str) -> ParsedDocument:
    return parse_markdown("source-test", "raw_src/test.md", markdown)


def test_segment_keeps_action_code_and_immediate_result_together():
    document = parsed(
        "## Enumerate SMB\n"
        "Observe port 445 before querying the service.\n\n"
        "```bash\n"
        "smbclient -L //TARGET_IP\n"
        "```\n\n"
        "The output reveals a public share worth inspecting.\n\n"
        + "Independent follow-up. " * 12
    )

    segments = segment_document(document, maximum_segment_chars=150)

    command_segment = next(segment for segment in segments if "smbclient" in segment.text)
    assert "Observe port 445" in command_segment.text
    assert "public share" in command_segment.text
    assert command_segment.block_indices == tuple(
        range(command_segment.block_indices[0], command_segment.block_indices[-1] + 1)
    )


def test_repeated_code_result_pairs_form_bounded_non_overlapping_units():
    body = "# Procedure\n" + "\n\n".join(
        f"```sh\nrun-{index}\n```\n\nResult {index}." for index in range(8)
    )
    document = parsed(body)

    segments = segment_document(document, maximum_segment_chars=100)

    assert len(segments) > 1
    assert all(len(segment.text) <= 100 for segment in segments)
    flattened = tuple(index for segment in segments for index in segment.block_indices)
    assert flattened == tuple(range(len(document.blocks)))
    assert len(flattened) == len(set(flattened))
    for index in range(8):
        containing = next(segment for segment in segments if f"run-{index}" in segment.text)
        assert f"Result {index}." in containing.text


def test_long_section_splits_only_between_blocks():
    document = parsed("\n\n".join(("alpha " * 15, "beta " * 15, "gamma " * 15)))

    segments = segment_document(document, maximum_segment_chars=120)

    assert len(segments) >= 2
    assert all(segment.start_line <= segment.end_line for segment in segments)
    assert tuple(index for segment in segments for index in segment.block_indices) == (
        0,
        1,
        2,
    )
    assert all(
        any(block.text in segment.text for segment in segments)
        for block in document.blocks
    )


def test_single_oversized_block_is_kept_whole():
    source = "one " * 100
    document = parsed(source)

    segments = segment_document(document, maximum_segment_chars=20)

    assert len(segments) == 1
    assert segments[0].block_indices == (0,)
    assert segments[0].text == document.blocks[0].text
    assert len(segments[0].text) > 20


def test_heading_stack_handles_level_jumps_and_replacements():
    document = parsed(
        "# Machine\nintro\n\n"
        "### Deep\ndeep body\n\n"
        "#### Deeper\ndeeper body\n\n"
        "## Replacement\nreplacement body\n\n"
        "#### Jump Again\njump body\n"
    )

    segments = segment_document(document, maximum_segment_chars=1)

    assert tuple(segment.heading_path for segment in segments) == (
        ("Machine",),
        ("Machine", "Deep"),
        ("Machine", "Deep", "Deeper"),
        ("Machine", "Replacement"),
        ("Machine", "Replacement", "Jump Again"),
    )


def test_equal_or_higher_heading_starts_a_new_section_but_child_is_retained():
    document = parsed(
        "## First\nfirst body\n\n"
        "### Child\nchild body\n\n"
        "## Second\nsecond body\n\n"
        "# Top\ntop body\n"
    )

    segments = segment_document(document)

    assert len(segments) == 3
    assert segments[0].heading_path == ("First",)
    assert "Child" in segments[0].text
    assert segments[1].heading_path == ("Second",)
    assert segments[2].heading_path == ("Top",)


def test_preamble_no_heading_and_empty_documents_are_deterministic():
    with_preamble = segment_document(parsed("preamble\n\n# Title\nbody\n"))
    without_heading = segment_document(parsed("first\n\nsecond\n"))
    empty = segment_document(parsed(""))

    assert tuple(segment.heading_path for segment in with_preamble) == ((), ("Title",))
    assert len(without_heading) == 1
    assert without_heading[0].heading_path == ()
    assert empty == ()


def test_segment_assets_are_exactly_the_assets_overlapping_its_span():
    document = parsed(
        "## Evidence\n"
        "![service banner](images/banner.png \"Banner\")\n\n"
        "## Next\nNo image here.\n"
    )

    first, second = segment_document(document)

    assert first.assets == document.assets
    assert first.assets[0].target == "images/banner.png"
    assert second.assets == ()
    assert first.start_line == min(
        document.blocks[index].start_line for index in first.block_indices
    )
    assert first.end_line == max(
        document.blocks[index].end_line for index in first.block_indices
    )


@pytest.mark.parametrize("maximum", [0, -1])
def test_maximum_segment_chars_must_be_positive(maximum: int):
    with pytest.raises(ValueError, match="positive"):
        segment_document(parsed("text"), maximum_segment_chars=maximum)


def test_segment_redacts_htb_flags_from_prose_code_and_heading():
    document = parsed(
        "## HTB{heading_secret}\n"
        "Prose HTB{prose_secret} and htb{another_secret}.\n\n"
        "```text\nHTB{code_secret}\n```\n"
    )

    segment = segment_document(document)[0]

    assert "htb{" not in segment.text.casefold()
    assert segment.text.count("<EXCLUDED_FLAG>") == 4


def test_htb_flags_support_embedded_prefixes_and_bounded_multiline_values():
    multiline = "prefixHTB{first line\nsecond line} suffix"
    malformed = "HTB{" + "x" * 513 + "}"

    sanitized = sanitize_searchable_text(
        f"{multiline}\nHTB{{first}}tail}} and hTb{{second}}\n{malformed}",
        (),
    )

    assert sanitized.startswith("prefix<EXCLUDED_FLAG> suffix")
    assert "<EXCLUDED_FLAG>tail}" in sanitized
    assert sanitized.count("<EXCLUDED_FLAG>") == 3
    assert malformed in sanitized


def test_multiline_htb_flag_inside_code_is_removed_from_segment_text():
    document = parsed("```text\nlabelHTB{line one\nline two}\n```\n")

    segment = segment_document(document)[0]

    assert "htb{" not in segment.text.casefold()
    assert segment.text == "label<EXCLUDED_FLAG>"


def test_nested_flag_heading_is_sanitized_with_its_active_block_path():
    token = "ABCDEF0123456789ABCDEF0123456789"
    document = parsed(
        "# Machine\n"
        "An unrelated checksum 0123456789abcdef0123456789abcdef remains.\n\n"
        "### Root Flag\n"
        f"The final value is `{token}`.\n"
    )

    segment = segment_document(document)[0]

    assert "0123456789abcdef0123456789abcdef" in segment.text
    assert token not in segment.text
    assert "<EXCLUDED_FLAG>" in segment.text


def test_known_contextual_hex_flag_is_excluded_from_every_document_occurrence():
    token = "ac97c7c414c770a5783e5126fa9cf77b"
    document = parsed(
        "# Machine\n"
        "## Task 7\n"
        "Submit the flag from the user home directory.\n\n"
        f"```text\n{token}\n```\n\n"
        "## User Flag\n"
        f"```text\n{token}\n```\n"
    )

    searchable = "\n".join(segment.text for segment in segment_document(document))

    assert token not in searchable
    assert searchable.count("<EXCLUDED_FLAG>") == 2


@pytest.mark.parametrize(
    ("heading", "token"),
    [
        (
            "Submit the flag located in the makis user's home directory.",
            "ac97c7c414c770a5783e5126fa9cf77b",
        ),
        (
            "Submit the flag located in root's home directory.",
            "b021c9a53a054900abcecf87e74be2f6",
        ),
        (
            "I'm Root user let try to read Root flag",
            "b021c9a53a054900abcecf87e74be2f6",
        ),
        ("we got our root flag", "b021c9a53a054900abcecf87e74be2f6"),
        (
            "OK Let find User flag, I'm going to use find command",
            "ac97c7c414c770a5783e5126fa9cf77b",
        ),
        ("Here is our User flag", "ac97c7c414c770a5783e5126fa9cf77b"),
        ("Let find User Flag", "de308dd04225c886c8ca6b6a2ff92944"),
        ("Here is our user flag", "de308dd04225c886c8ca6b6a2ff92944"),
        ("Here is our Root Flag", "2e75395638c246ce3f50b67f55329255"),
        ("user.txt", "de308dd04225c886c8ca6b6a2ff92944"),
        ("root.txt", "2e75395638c246ce3f50b67f55329255"),
    ],
)
def test_real_lame_and_permx_descriptive_flag_headings_are_contextual(
    heading: str,
    token: str,
):
    assert sanitize_searchable_text(token, (heading,)) == "<EXCLUDED_FLAG>"


@pytest.mark.parametrize(
    ("markdown", "known_flags"),
    [
        (
            "# Task 7:\n"
            "## Submit the flag located in the makis user's home directory.\n"
            "```text\nac97c7c414c770a5783e5126fa9cf77b\n```\n"
            "# Task 8:\n"
            "## Submit the flag located in root's home directory.\n"
            "```text\nb021c9a53a054900abcecf87e74be2f6\n```\n",
            (
                "ac97c7c414c770a5783e5126fa9cf77b",
                "b021c9a53a054900abcecf87e74be2f6",
            ),
        ),
        (
            "# Let start with Nmap scan\n"
            "## Let find User Flag\n"
            "## Here is our user flag\n"
            "```text\nmtz@permx:~$ cat /home/mtz/user.txt\n"
            "de308dd04225c886c8ca6b6a2ff92944\n```\n"
            "## Here is our Root Flag\n"
            "```text\nroot@permx:/home/mtz# cat /root/root.txt\n"
            "2e75395638c246ce3f50b67f55329255\n```\n",
            (
                "de308dd04225c886c8ca6b6a2ff92944",
                "2e75395638c246ce3f50b67f55329255",
            ),
        ),
    ],
)
def test_real_lame_and_permx_flag_line_shapes_do_not_reach_logical_segments(
    markdown: str,
    known_flags: tuple[str, ...],
):
    document = parsed(markdown)
    original = document.model_dump(mode="json")

    segments = segment_document(document, maximum_segment_chars=80)
    searchable_values = tuple(
        value
        for segment in segments
        for value in (segment.text, *segment.heading_path)
    )

    assert all(
        token not in value for token in known_flags for value in searchable_values
    )
    assert document.model_dump(mode="json") == original


@pytest.mark.parametrize(
    "heading_path",
    [
        ("user flag",),
        (" ROOT   FLAG ",),
        ("Final-Flag",),
        ("root_flag",),
        ("flag",),
        ("Machine", "UsEr"),
        ("Machine", "ROOT"),
    ],
)
def test_contextual_32_hex_flags_are_redacted_case_and_whitespace_insensitively(
    heading_path: tuple[str, ...],
):
    token = "abcdef0123456789abcdef0123456789"

    sanitized = sanitize_searchable_text(f"value: {token}", heading_path)

    assert token not in sanitized
    assert sanitized == "value: <EXCLUDED_FLAG>"


def test_32_hex_hashes_survive_unrelated_technical_contexts():
    token = "abcdef0123456789abcdef0123456789"

    assert sanitize_searchable_text(f"MD5: {token}", ("Password hashes",)) == (
        f"MD5: {token}"
    )


@pytest.mark.parametrize(
    "heading",
    [
        "TCP flags",
        "Compiler flags",
        "Command-line flags",
        "Feature flags",
        "Root cause analysis",
    ],
)
def test_technical_flag_headings_do_not_create_hex_flag_context(heading: str):
    token = "abcdef0123456789abcdef0123456789"

    assert sanitize_searchable_text(token, (heading,)) == token


def test_only_standalone_32_hex_values_are_contextually_redacted():
    token = "abcdef0123456789abcdef0123456789"
    text = f"standalone {token}; embedded prefix{token}suffix; longer {token}aa"

    sanitized = sanitize_searchable_text(text, ("Root Flag",))

    assert sanitized == (
        f"standalone <EXCLUDED_FLAG>; embedded prefix{token}suffix; longer {token}aa"
    )


def test_sanitization_does_not_mutate_the_parsed_document():
    raw = "## Root Flag\n\n```text\nHTB{do_not_index_me}\n```\n"
    document = parsed(raw)
    original_dump = document.model_dump(mode="json")

    segment = segment_document(document)[0]

    assert "HTB{" not in segment.text
    assert document.model_dump(mode="json") == original_dump
    assert any("HTB{" in block.text for block in document.blocks)


def test_logical_heading_paths_are_sanitized_but_raw_headings_are_unchanged():
    htb_token = "HTB{heading_secret}"
    hex_token = "abcdef0123456789abcdef0123456789"
    parent_hex = "0123456789abcdef0123456789abcdef"
    document = parsed(
        f"# Parent {htb_token} {parent_hex}\nparent body\n\n"
        f"### Root Flag {hex_token}\nchild body\n"
    )
    original = document.model_dump(mode="json")

    segments = segment_document(document, maximum_segment_chars=1)

    assert segments[0].heading_path == (
        f"Parent <EXCLUDED_FLAG> {parent_hex}",
    )
    assert segments[1].heading_path == (
        "Parent <EXCLUDED_FLAG> <EXCLUDED_FLAG>",
        "Root Flag <EXCLUDED_FLAG>",
    )
    assert all(
        "htb{" not in component.casefold() and hex_token not in component
        for segment in segments
        for component in segment.heading_path
    )
    assert document.model_dump(mode="json") == original
    assert document.blocks[0].text == f"Parent {htb_token} {parent_hex}"
    assert document.blocks[2].text == f"Root Flag {hex_token}"


def test_empty_heading_affects_structure_without_empty_path_component():
    document = parsed("#\nbody\n")

    segment = segment_document(document)[0]

    assert segment.block_indices == (0, 1)
    assert segment.heading_path == ()
    assert "body" in segment.text


def test_manual_nested_blocks_keep_exact_contiguous_indices_and_spans():
    document = ParsedDocument(
        source_id="manual",
        path="raw_src/manual.md",
        blocks=(
            ParsedBlock(kind=BlockKind.HEADING, text="Root", start_line=1, end_line=1, level=1),
            ParsedBlock(kind=BlockKind.PARAGRAPH, text="before", start_line=2, end_line=4),
            ParsedBlock(kind=BlockKind.BLOCKQUOTE, text="quote", start_line=5, end_line=9),
            ParsedBlock(kind=BlockKind.PARAGRAPH, text="inside", start_line=6, end_line=6),
        ),
    )

    segment = segment_document(document)[0]

    assert segment.block_indices == (0, 1, 2, 3)
    assert (segment.start_line, segment.end_line) == (1, 9)

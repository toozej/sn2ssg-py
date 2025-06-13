from unittest.mock import patch, mock_open
import os
import subprocess

from sn2ssg import (
    _write_note_file,
    _ensure_num_parsed_notes_matches_outputted_notes,
    _send_gotify_notification,
    _get_note_header,
    _process_note,
    _gather_header_info,
    _convert_title_to_filename,
    _convert_title_to_slug,
    _split_notes,
    _convert_date_format,
    _create_ssg_header,
    _prepend_ssg_header,
    _trash_sncli_log,
    _delete_existing_title,
    _delete_existing_header,
    _exponential_backoff_delay,
    _run_sncli_with_backoff,
    _validate_dumped_notes_have_tag_to_download_with_backoff,
)


def test_write_note_file_new_content(tmp_path):
    output_markdown = ["# Title\n", "Content\n"]
    output_dir = str(tmp_path)
    output_filename = "note.md"
    file_path = os.path.join(output_dir, output_filename)

    _write_note_file(output_markdown, output_dir, output_filename)

    with open(file_path) as f:
        content = f.read()

    assert content == "# Title\nContent\n"


def test_write_note_file_existing_same_content(tmp_path):
    output_markdown = ["# Title\n", "Content\n"]
    output_dir = str(tmp_path)
    output_filename = "note.md"
    file_path = os.path.join(output_dir, output_filename)

    with open(file_path, "w") as f:
        f.write("# Title\nContent\n")

    with patch("builtins.print") as mocked_print:
        _write_note_file(output_markdown, output_dir, output_filename)
        mocked_print.assert_called_with(
            f"The file '{file_path}' already exists with the same content."
        )


# TODO fix usage of temporary directories
# def test_write_note_file_existing_different_content(tmp_path):
#     output_markdown = ["# Title\n", "New Content\n"]
#     output_dir = str(tmp_path)
#     output_filename = "note.md"
#     file_path = os.path.join(output_dir, output_filename)
#
#     with open(file_path, "w") as f:
#         f.write("# Title\nOld Content\n")
#
#     with patch('builtins.print') as mocked_print:
#         _write_note_file(output_markdown, output_dir, output_filename)
#         mocked_print.assert_called_with(f"Content differs from the existing file '{file_path}'. Overwriting the file.")


def test_ensure_num_parsed_notes_matches_outputted_notes_matching():
    with patch("builtins.print") as mocked_print:
        _ensure_num_parsed_notes_matches_outputted_notes(3, 3, 0, 5)
        mocked_print.assert_called_with("Number of parsed vs outputted notes matches: 3 notes")


# TODO stop sending Gotify notifications during testing
# def test_ensure_num_parsed_notes_matches_outputted_notes_mismatch():
#     with patch('builtins.print') as mocked_print, patch('sys.exit') as mocked_exit:
#         _ensure_num_parsed_notes_matches_outputted_notes(3, 2, 0, 5)
#         mocked_print.assert_any_call(
#             "FATAL: The number of notes (3) does not match the number of outputted files (2)")
#         mocked_exit.assert_called_with(1)


def test_send_gotify_notification_no_url_token():
    with patch("builtins.print") as mocked_print:
        before_token_value = os.environ.get("GOTIFY_TOKEN")
        # remove GOTIFY_TOKEN environment variable such that test is relevant
        os.environ["GOTIFY_TOKEN"] = ""

        _send_gotify_notification("Test Title", "Test Message")
        mocked_print.assert_called_with(
            "Gotify URL and/or token not specified in environment. No notification was sent"
        )
        # set GOTIFY_TOKEN environment variable back after test
        os.environ["GOTIFY_TOKEN"] = before_token_value


def test_send_gotify_notification_with_url_token():
    with (
        patch.dict(os.environ, {"GOTIFY_URL": "http://fakeurl.com", "GOTIFY_TOKEN": "faketoken"}),
        patch("requests.post") as mocked_post,
    ):
        _send_gotify_notification("Test Title", "Test Message")
        mocked_post.assert_called_once_with(
            "http://fakeurl.com/message",
            params={"token": "faketoken"},
            data={"title": "Test Title", "message": "Test Message"},
        )


def test_get_note_header():
    note = ["| Header 1 |", "Content line 1", "+- Header 2 -+", "Content line 2"]
    header = _get_note_header(note)
    assert header == ["| Header 1 |", "+- Header 2 -+"]


def test_process_note():
    note = [
        "| Title: Test Note |",
        "| Date: Fri, 01 Sep 2023 02:33:35 |",
        "| Tags: tag1, tag2 |",
        "+- Header -+",
        "Content line 1",
        "Content line 2",
    ]

    with (
        patch(
            "os.environ.get",
            side_effect=lambda k, v=None: {
                "AUTHOR": "test_author",
                "SSG_TYPE": "test_ssg",
                "OUTPUT_DIR": "/fake/dir",
            }.get(k, v),
        ),
        patch("builtins.print"),
        patch("builtins.open", mock_open()),
        patch("os.path.exists", return_value=False),
    ):
        result = _process_note(note)
        assert result == 1


def test_gather_header_info():
    note = [
        "| Title: Test Note |",
        "| Date: Fri, 01 Sep 2023 02:33:35 |",
        "| Tags: tag1,tag2 |",
        "Content line 1",
    ]
    pattern = r"\| (.*?): (.*?) \|"
    title, date, tags = _gather_header_info(pattern, note)
    assert title == "Test Note"
    assert date == "Fri, 01 Sep 2023 02:33:35"
    assert tags == ["tag1", "tag2"]


def test_convert_title_to_filename():
    title = "Test Note"
    filename = _convert_title_to_filename(title)
    assert filename == "test-note.md"


def test_convert_title_to_slug():
    title = "Test Note!"
    slug = _convert_title_to_slug(title)
    assert slug == "test-note"


def test_split_notes():
    input_lines = [
        "+--------------------------------------------------------------+",
        "| Title: Note 1 |",
        "+--------------------------------------------------------------+",
        "Content line 1",
        "+--------------------------------------------------------------+",
        "| Title: Note 2 |",
        "+--------------------------------------------------------------+",
        "Content line 2",
    ]
    notes = _split_notes(input_lines)
    print(notes)
    assert notes == [
        [
            "+--------------------------------------------------------------+",
            "| Title: Note 1 |",
            "+--------------------------------------------------------------+",
            "Content line 1",
        ],
        [
            "+--------------------------------------------------------------+",
            "| Title: Note 2 |",
            "+--------------------------------------------------------------+",
            "Content line 2",
        ],
    ]


def test_convert_date_format():
    input_date = "Fri, 01 Sep 2023 02:33:35"
    output_date = _convert_date_format(input_date)
    assert output_date == "2023-09-01T02:33:35+00:00"


def test_create_ssg_header():
    ssg_type = "test"
    title = "Test Note"
    subtitle = "Subtitle"
    author = "Author"
    date = "2023-09-01T02:33:35+00:00"
    tags = ["tag1", "tag2"]

    template_content = """---
title: {{title}}
author: {{author}}
type: post
unlisted: {{unlisted}}
date: {{date}}
url: /{{slug}}/
summary: {{summary}}
categories:
  - {{tag}}
---"""

    with patch("builtins.open", mock_open(read_data=template_content)):
        header = _create_ssg_header(ssg_type, title, subtitle, author, date, tags)

    expected_header = [
        "---",
        "title: Test Note",
        "author: Author",
        "type: post",
        "unlisted: false",
        "date: 2023-09-01T02:33:35+00:00",
        "url: /test-note/",
        "summary: ",
        "categories:",
        "  - tag1",
        "  - tag2",
        "---",
    ]

    assert header == expected_header


def test_create_ssg_header_unlisted():
    ssg_type = "test"
    title = "Test Note - Thought"
    subtitle = "Subtitle"
    author = "Author"
    date = "2023-09-01T02:33:35+00:00"
    tags = ["tag1", "thoughts"]

    template_content = """---
title: {{title}}
author: {{author}}
type: post
unlisted: {{unlisted}}
date: {{date}}
url: /{{slug}}/
summary: {{summary}}
categories:
  - {{tag}}
---"""

    with patch("builtins.open", mock_open(read_data=template_content)):
        header = _create_ssg_header(ssg_type, title, subtitle, author, date, tags)

    expected_header = [
        "---",
        "title: Test Note - Thought",
        "author: Author",
        "type: post",
        "unlisted: true",
        "date: 2023-09-01T02:33:35+00:00",
        "url: /test-note-thought/",
        "summary: ",
        "categories:",
        "  - tag1",
        "  - thoughts",
        "---",
    ]

    assert header == expected_header


def test_prepend_ssg_header():
    new_header = [
        "---",
        "title: Test Note",
        "subtitle: Subtitle",
        "author: Author",
        "date: 2023-09-01T02:33:35+00:00",
        "tags: tag1",
        "  - tag2",
        "slug: test-note",
        "summary: Test Note",
        "---",
    ]

    note = ["Content line 1", "Content line 2"]

    output_note = _prepend_ssg_header(new_header, note)

    expected_output = [
        "---\n",
        "title: Test Note\n",
        "subtitle: Subtitle\n",
        "author: Author\n",
        "date: 2023-09-01T02:33:35+00:00\n",
        "tags: tag1\n",
        "  - tag2\n",
        "slug: test-note\n",
        "summary: Test Note\n",
        "---\n",
        "Content line 1\n",
        "Content line 2\n",
    ]

    assert output_note == expected_output


def test_trash_sncli_log():
    input_lines = """
sncli database doesn't exist
Starting full sync
Synced new note from server
Saved note to disk
Full sync completed
Other log entry
"""
    expected_output = ["", "Other log entry", ""]

    output = _trash_sncli_log(input_lines)
    assert output == expected_output


def test_delete_existing_title():
    note = ["# Title", "Content line 1", "Content line 2"]
    expected_output = ["Content line 1", "Content line 2"]

    output = _delete_existing_title(note)
    assert output == expected_output

    note_without_title = ["Content line 1", "Content line 2"]

    output = _delete_existing_title(note_without_title)
    assert output == note_without_title


def test_delete_existing_header():
    note = [
        "| Header 1 |",
        "Content line 1",
        "+- Header 2 -+",
        "Content line 2",
        "| Header 3 |",
        "Content line 3",
    ]
    expected_output = ["Content line 1", "Content line 2", "Content line 3"]

    output = _delete_existing_header(note)
    assert output == expected_output


# TODO mock sncli valid output
# def test_run_sncli_success():
#     tag_to_download = "test-tag"
#     input_filename = "output.txt"
#     sncli_path = "/usr/local/bin/sncli"
#
#     with patch("shutil.which", return_value=sncli_path):
#         with patch("builtins.open", mock_open()) as mocked_file:
#             with patch("subprocess.run") as mocked_subprocess:
#                 _run_sncli(tag_to_download, input_filename)
#
#                 mocked_file.assert_called_with(input_filename, "w")
#                 mocked_subprocess.assert_called_with(
#                     [sncli_path, "--config=/dev/null", "-r", "dump", tag_to_download],
#                     stdout=mocked_file(),
#                     check=True,
#                     text=True
#                 )
#
#
# TODO mock sncli error output
# def test_run_sncli_error():
#     tag_to_download = "test-tag"
#     input_filename = "output.txt"
#     sncli_path = "/usr/local/bin/sncli"
#
#     with patch("shutil.which", return_value=sncli_path):
#         with patch("builtins.open", mock_open()) as mocked_file:
#             with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "cmd")) as mocked_subprocess:
#                 with patch("builtins.print") as mocked_print:
#                     _run_sncli(tag_to_download, input_filename)
#
#                     mocked_file.assert_called_with(input_filename, "w")
#                     mocked_subprocess.assert_called_with(
#                         [sncli_path, "--config=/dev/null", "-r", "dump", tag_to_download],
#                         stdout=mocked_file(),
#                         check=True,
#                         text=True
#                     )
#                     mocked_print.assert_called_with("Error: Command 'cmd' returned non-zero exit status 1.")


def test_validate_dumped_notes_have_tag_to_download_with_backoff():
    tag_to_download = "test-tag"
    input_lines = ["| Title: Note 1 |", "| Tags: test-tag,other-tag |", "Content line 1"]

    assert (
        _validate_dumped_notes_have_tag_to_download_with_backoff(tag_to_download, input_lines)
        is False
    )

    input_lines_multiple_notes = [
        "| Title: Note 1 |",
        "| Tags: test-tag,other-tag |",
        "Content line 1",
        "| Title: Note 2 |",
        "| Tags: other-tag |",
        "Content line 2",
    ]

    assert (
        _validate_dumped_notes_have_tag_to_download_with_backoff(
            tag_to_download, input_lines_multiple_notes
        )
        is False
    )

    input_lines_without_tag = [
        "| Title: Note 1 |",
        "| Tags: other-tag |",
        "Content line 1",
    ]

    assert (
        _validate_dumped_notes_have_tag_to_download_with_backoff(
            tag_to_download, input_lines_without_tag
        )
        is False
    )

    input_lines_without_tags_line = [
        "| Title: Note 1 |",
        "Content line 1",
    ]

    assert (
        _validate_dumped_notes_have_tag_to_download_with_backoff(
            tag_to_download, input_lines_without_tags_line
        )
        is False
    )


def test_exponential_backoff_delay_basic():
    # Test that delay increases exponentially and is capped
    delays = [_exponential_backoff_delay(i, base_delay=1.0, max_delay=10.0) for i in range(5)]
    assert all(delay > 0 for delay in delays)
    assert delays[0] < delays[1] < delays[2] <= 10.0
    # Test that delay never exceeds max_delay
    assert all(delay <= 10.0 for delay in delays)
    # Test that delay is never less than 0.1
    assert all(delay >= 0.1 for delay in delays)


def test_run_sncli_with_backoff_success(tmp_path):
    input_filename = str(tmp_path / "sncli_output.txt")
    tag_to_download = "sometag"
    with (
        patch("shutil.which", return_value="/usr/bin/sncli"),
        patch("subprocess.run") as mock_run,
        patch("builtins.open", mock_open()),
    ):
        mock_run.return_value = None
        result = _run_sncli_with_backoff(tag_to_download, input_filename)
        assert result is True
        mock_run.assert_called()


def test_run_sncli_with_backoff_failure(tmp_path):
    input_filename = str(tmp_path / "sncli_output.txt")
    tag_to_download = "sometag"
    with (
        patch("shutil.which", return_value="/usr/bin/sncli"),
        patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "cmd")),
        patch("builtins.open", mock_open()),
        patch("time.sleep") as mock_sleep,
    ):
        result = _run_sncli_with_backoff(tag_to_download, input_filename)
        assert result is False
        assert mock_sleep.called


def test_validate_dumped_notes_have_tag_to_download_with_backoff_success(tmp_path):
    input_filename = str(tmp_path / "input.md")
    tag_to_download = "sometag"
    file_content = "| Tags: sometag,othertag |\n| Title: Note |\n"
    with (
        patch("builtins.open", mock_open(read_data=file_content)),
        patch("time.sleep") as mock_sleep,
        patch("sn2ssg._run_sncli_with_backoff", return_value=True),
    ):
        result = _validate_dumped_notes_have_tag_to_download_with_backoff(
            tag_to_download, input_filename
        )
        assert result is True
        mock_sleep.assert_not_called()


def test_validate_dumped_notes_have_tag_to_download_with_backoff_failure(tmp_path):
    input_filename = str(tmp_path / "input.md")
    tag_to_download = "sometag"
    # No tags line in file
    file_content = "| Title: Note |\nContent\n"
    with (
        patch("builtins.open", mock_open(read_data=file_content)),
        patch("time.sleep") as mock_sleep,
        patch("sn2ssg._run_sncli_with_backoff", return_value=True),
    ):
        result = _validate_dumped_notes_have_tag_to_download_with_backoff(
            tag_to_download, input_filename
        )
        assert result is False
        assert mock_sleep.called

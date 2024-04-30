#!/usr/bin/env python3
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime

import requests

SN_HEADER_PATTERN = r"^\|\s*(.*?):\s*(.*?)\s*\|"
IGNORED_TAGS = [os.environ.get("TAG_TO_DOWNLOAD"), "blog"]


def _trash_sncli_log(input_lines: [str]) -> [str]:
    log_entries_to_remove = [
        "sncli database doesn't exist",
        "Starting full sync",
        "Synced new note from server",
        "Saved note to disk",
        "Full sync completed",
    ]
    output_lines = list(
        filter(
            lambda s: "" in s and not any(x in s for x in log_entries_to_remove),
            input_lines.split("\n"),
        )
    )
    return output_lines


def _gather_header_info(pattern: str, note: [str]) -> tuple[str, str, [str]]:
    """
    header info we care about: title, date, tags without the one we filtered all notes for
    """
    title = ""
    date = ""
    tags = []
    for line in note:
        match = re.match(pattern, line)
        if match:
            key = match.group(1).strip()
            value = match.group(2).strip()
            if key == "Title":
                title = value.replace("#", "")
            elif key == "Date":
                date = value
            elif key == "Tags":
                for item in value.split(","):
                    tags.append(item)
    return title, date, tags


def _convert_title_to_filename(title: str) -> str:
    return _convert_title_to_slug(title) + ".md"


def _convert_title_to_slug(title: str) -> str:
    # Remove special characters, keep only alphanumeric and whitespace
    slug = re.sub(r"[^\w\s-]", "", title)

    # Replace whitespace with "-"
    slug = slug.strip().replace(" ", "-")

    # Replace consecutive "-" with a single "-"
    slug = re.sub(r"[-]+", "-", slug)

    # Convert to lowercase
    slug = slug.lower()

    return slug


def _split_notes(input_lines: [str]) -> [[str]]:
    # uses "ending" header (ends with "-+") lines as delimiters between notes
    output_notes = []
    note = []
    header_start_found = False
    header_end_found = False
    for line in input_lines:
        if (
            line.endswith("-+")
            and header_start_found is True
            and header_end_found is True
        ):
            # add current temp note to list of notes
            output_notes.append(note)
            # cut a new note
            note = []
            header_start_found = True
            header_end_found = False
            note.append(line)
        else:
            if line.endswith("-+") and header_start_found is False:
                header_start_found = True
            elif (
                line.endswith("-+")
                and header_start_found is True
                and header_end_found is False
            ):
                header_end_found = True
            # add line to current temp note
            note.append(line)

    # since we're basing spitting of notes on headers, we won't have a way to store the last note
    # so explicitly store the last note
    output_notes.append(note)
    return output_notes


def _delete_existing_title(note: [str]) -> [str]:
    # delete title line of markdown note if it starts with "# "
    # since the title will already be part of header, and we don't want to display it twice
    if note[0].startswith("# "):
        return note[1:]
    else:
        return note


def _delete_existing_header(note: [str]) -> [str]:
    # trash "starting" header and "middle" header lines
    output_note = []
    for line in note:
        if (
            not line.startswith("|")
            and not line.endswith("|")
            and not line.startswith("+-")
        ):
            output_note.append(line)
    return output_note


def _convert_date_format(input_date: str) -> str:
    # convert input_date from format: Fri, 01 Sep 2023 02:33:35
    # to format: 2018-07-25T03:25:58+00:00
    return datetime.strptime(input_date, "%a, %d %b %Y %H:%M:%S").strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )


def _determine_tags(tags: [str]) -> str:
    # don't template the "tag" field with the meta-tag TAG_TO_DOWNLOAD
    # either use the next tag, the CONTINUOUS_NOTE_TAG with the "blog" prefix stripped, or set as "Uncategorized"
    if (
        isinstance(tags, list)
        and all(isinstance(item, str) for item in tags)
        and len(tags) > 1
    ):
        for tag in tags:
            if tag not in IGNORED_TAGS:
                if tag == os.environ.get("CONTINUOUS_NOTE_TAG"):
                    for t in IGNORED_TAGS:
                        # this strips the prefix "blog" from the CONTINUOUS_NOTE_TAG
                        # allowing for a cleaner, more accurate tag
                        tag = tag.lstrip(f"{t}:")
                return tag


def _create_ssg_header(
    ssg_type: str, title: str, subtitle: str, author: str, date: str, tags: [str]
) -> [str]:
    with open(f"templates/{ssg_type}.md") as template_file:
        template = template_file.read()

    try:
        # Replace template fields with user input
        template = template.replace("{{title}}", title)
        template = template.replace("{{subtitle}}", subtitle)
        template = template.replace("{{author}}", author)
        template = template.replace("{{date}}", date)
        template = template.replace("{{slug}}", _convert_title_to_slug(title))

        tag = _determine_tags(tags)
        if tag:
            template = template.replace("{{tag}}", tag)
        else:
            template = template.replace("{{tag}}", "Uncategorized")
    except:
        print("Field missing when templating SSG header, carrying on!")

    return [x for x in template.split("\n")]


def _prepend_ssg_header(new_header: [str], note: [str]) -> [str]:
    output_note = []
    for line in new_header + note:
        output_note.append(line + "\n")
    return output_note


def _write_note_file(
    output_markdown: [str],
    output_dir: str,
    output_filename: str,
    notes_output_counter: int,
):
    # Write the resulting markdown to a file
    with open(f"{output_dir}/{output_filename}", "w") as output_file:
        for line in output_markdown:
            output_file.write(line)
    return notes_output_counter + 1


def _ensure_num_parsed_notes_matches_outputted_notes(
    notes: [[str]], notes_output_counter: int, start_counter: int, end_counter: int
) -> int:
    if len(notes) != notes_output_counter or end_counter < start_counter:
        title = "sn2ssg FATAL error"
        message = f"FATAL: The number of notes ({len(notes)}) does not match the number of outputted files ({notes_output_counter})"
        print(message)
        _send_gotify_notification(title, message)
        sys.exit(1)
    elif os.environ.get("DEBUG") == "True":
        title = "sn2ssg successful"
        message = (
            f"DEBUG: Number of parsed vs outputted notes matches: {len(notes)} notes"
        )
        print(message)
        _send_gotify_notification(title, message)
    else:
        message = f"Number of parsed vs outputted notes matches: {len(notes)} notes"
        print(message)


def _run_sncli(tag_to_download: str, input_filename: str):
    # Define the command as a list of strings
    sncli_binary_path = shutil.which("sncli")

    # If it doesn't exist in the PATH, use the default path
    if sncli_binary_path is None:
        sncli_binary_path = "/home/james/.local/bin/sncli"

    command = [sncli_binary_path, "--config=/dev/null", "-r", "dump", tag_to_download]

    # Execute the command
    try:
        with open(input_filename, "w") as output:
            subprocess.run(command, stdout=output, check=True, text=True)
        print("Dumping of notes via sncli was successful.")
    except subprocess.CalledProcessError as e:
        print(f"Error: {e}")


def _send_gotify_notification(notification_title: str, notification_text: str):
    if os.environ.get("GOTIFY_URL") and os.environ.get("GOTIFY_TOKEN"):
        url = f"{os.environ.get('GOTIFY_URL')}/message"
    else:
        print(
            "Gotify URL and/or token not specified in environment. No notification was sent"
        )
        return
    params = {"token": os.environ.get("GOTIFY_TOKEN")}
    data = {"title": notification_title, "message": notification_text}
    response = requests.post(url, params=params, data=data)
    print(response.text)


def _get_note_header(note: [str]) -> [str]:
    # TODO attempt to collapse _get_note_header and _gather_header_info functions
    # TODO split out getting header related info into different functions such that they can be mix-matched?
    return [
        line
        for line in note
        if line.startswith("|") or line.endswith("|") or line.startswith("+-")
    ]


def _adjust_note_header_title(note_header: [str], title_addition: str) -> [str]:
    """
    Adjust given note_header by appending title_addition to the end of the found Title field within
    note_header

    :param note_header: note header to be adjusted
    :param title_addition: string to append to end of title within note_header
    :return: adjusted note_header with title addition
    """
    count = 0
    for line in note_header:
        match = re.match(SN_HEADER_PATTERN, line)
        if match:
            key = match.group(1).strip()
            value = match.group(2).strip()
            if key == "Title":
                adjusted_title_line = re.sub(value, value + title_addition, line)
                note_header[count] = adjusted_title_line
                break
        count += 1
    return note_header


def _split_continuous_note(note: [str]) -> [[str]]:
    """
    splits up a single continuous note into multiple (one note per line after ssg_header)

    :param note: input continuous note to be split
    :return: list of notes each with same header and one line of continuous note
    """
    notes = []
    note_header = _get_note_header(note)
    note_body = note[len(note_header) + 1:]
    note_num_parts = len([s for s in note_body if s != ""])

    # make a new note each with note_header, but only one line of the input note
    for line in note_body:
        # skip empty/blank lines as continuous notes should be blank-line separated
        if line:
            note_tmp = (
                # TODO why are multiple note_num_parts being appended?
                _adjust_note_header_title(
                    _get_note_header(note), f" - {note_num_parts}"
                )
                + [""]
                + [line.strip()]
            )
            notes.append(note_tmp)
            note_num_parts -= 1
    return notes


def _process_note(note: [str], count: int) -> int:
    title, date, tags = _gather_header_info(SN_HEADER_PATTERN, note)
    note = _delete_existing_header(note)
    subtitle = ""
    author = os.environ.get("AUTHOR", "root")
    date = _convert_date_format(date)
    new_header = _create_ssg_header(
        os.environ.get("SSG_TYPE"), title, subtitle, author, date, tags
    )
    note = _delete_existing_title(note)
    note = _prepend_ssg_header(new_header, note)
    # TODO do I need count and notes_output_counter?
    notes_output_counter = _write_note_file(
        note,
        os.environ.get("OUTPUT_DIR"),
        _convert_title_to_filename(title),
        count,
    )
    return notes_output_counter


def main():
    # TODO add docstrings to all functions
    # TODO split various functions out into smaller files?

    # setup input and output dirs and input stats
    try:
        os.mkdir(os.environ.get("INPUT_DIR"))
        os.mkdir(os.environ.get("OUTPUT_DIR"))
    except FileExistsError:
        print("Input / Output directories already exist")
    output_dir_num_files_start = len(
        fnmatch.filter(os.listdir(os.environ.get("OUTPUT_DIR")), "*.*")
    )

    # get raw notes from Simplenote
    input_filename = f"{os.environ.get('INPUT_DIR')}/sn_dump.md"
    _run_sncli(os.environ.get("TAG_TO_DOWNLOAD"), input_filename)
    with open(input_filename) as input_file:
        input_lines = input_file.read()
    input_lines = _trash_sncli_log(input_lines)

    # split raw notes and process them
    notes = _split_notes(input_lines)
    notes_output_counter = 0
    for note in notes:
        _, _, tags = _gather_header_info(SN_HEADER_PATTERN, note)
        if os.environ.get("CONTINUOUS_NOTE_TAG") in tags:
            # only split continuous notes (not continuous notes themselves) get processed...
            notes.extend(_split_continuous_note(note))
            notes.remove(note)
            continue
        notes_output_counter += _process_note(note, notes_output_counter)

    # setup output stats and diff input vs output stats
    output_dir_num_files_end = len(
        fnmatch.filter(os.listdir(os.environ.get("OUTPUT_DIR")), "*.*")
    )
    _ensure_num_parsed_notes_matches_outputted_notes(
        notes,
        notes_output_counter,
        output_dir_num_files_start,
        output_dir_num_files_end,
    )

    # trash temporary raw notes input file
    print("deleting temporary raw notes input file")
    os.remove(input_filename)

    # sleep until the next execution
    time_to_sleep = int(os.environ.get("POLLING_CYCLE", 3600))
    print(f"sn2ssg ran successfully! Sleeping {time_to_sleep} before next cycle.")
    time.sleep(time_to_sleep)
    return


if __name__ == "__main__":
    main()

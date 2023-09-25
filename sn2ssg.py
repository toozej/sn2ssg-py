#!/usr/bin/env python3
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime

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

        # don't template the "tag" field with the meta-tag TAG_TO_DOWNLOAD
        # either use the next tag, or set as "Uncategorized"
        if (
            isinstance(tags, list)
            and all(isinstance(item, str) for item in tags)
            and len(tags) > 1
        ):
            for tag in tags:
                if tag not in IGNORED_TAGS:
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


def _ensure_num_parsed_notes_matches_output_notes(
    notes: [[str]], notes_output_counter: int
) -> int:
    if len(notes) != notes_output_counter:
        print(
            f"FATAL: The number of notes ({len(notes)}) does not match the number of outputted files ({notes_output_counter})"
        )
        sys.exit(1)


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


def main():
    try:
        os.mkdir(os.environ.get("INPUT_DIR"))
        os.mkdir(os.environ.get("OUTPUT_DIR"))
    except FileExistsError:
        print("Input / Output directories already exist")
    input_filename = f"{os.environ.get('INPUT_DIR')}/sn_dump.md"
    _run_sncli(os.environ.get("TAG_TO_DOWNLOAD"), input_filename)
    with open(input_filename) as input_file:
        input_lines = input_file.read()
    input_lines = _trash_sncli_log(input_lines)
    notes = _split_notes(input_lines)
    notes_output_counter = 0
    for note in notes:
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
        notes_output_counter = _write_note_file(
            note,
            os.environ.get("OUTPUT_DIR"),
            _convert_title_to_filename(title),
            notes_output_counter,
        )
    _ensure_num_parsed_notes_matches_output_notes(notes, notes_output_counter)
    os.remove(input_filename)
    time.sleep(int(os.environ.get("POLLING_CYCLE", 3600)))
    return


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import fnmatch
import os
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime

import requests

SN_HEADER_PATTERN = r"^\|\s*(.*?):\s*(.*?)\s*\|"
IGNORED_TAGS = [os.environ.get("TAG_TO_DOWNLOAD"), "blog"]
UNLISTED_TAGS = [item.split(":")[1] for item in os.environ.get("UNLISTED_TAGS").split(",")]
TITLE_TO_SUMMARY_SUBSTITUTIONS = [
    tuple(item.split(":")) for item in os.environ.get("TITLE_SUBSTITUTIONS").split(",")
]

# Exponential backoff configuration
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", 5))
BASE_DELAY = float(os.environ.get("BASE_DELAY", 1.0))
MAX_DELAY = float(os.environ.get("MAX_DELAY", 300.0))  # 5 minutes max


def _exponential_backoff_delay(
    attempt: int, base_delay: float = BASE_DELAY, max_delay: float = MAX_DELAY
) -> float:
    """
    Calculate exponential backoff delay with jitter.

    :param attempt: Current attempt number (0-based)
    :param base_delay: Base delay in seconds
    :param max_delay: Maximum delay in seconds
    :return: Delay in seconds
    """
    # Calculate exponential delay: base_delay * (2 ^ attempt)
    delay = base_delay * (2**attempt)

    # Cap at max_delay
    delay = min(delay, max_delay)

    # Add jitter (±25% of the delay)
    jitter = delay * 0.25 * (2 * random.random() - 1)
    delay += jitter

    # Ensure delay is positive
    return max(0.1, delay)


def _trash_sncli_log(input_lines: list[str]) -> list[str]:
    """
    Filters out Simplenote log entries from the input lines.

    :param input_lines: List of strings, each representing a line from the sncli log.
    :return: List of strings with the specified log entries removed.
    """
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


def _gather_header_info(pattern: str, note: list[str]) -> tuple[str, str, list[str]]:
    """
    Extracts header information (title, date, and tags) from a note.

    :param pattern: Regex pattern to match the header lines.
    :param note: List of strings, each representing a line from a note.
    :return: A tuple containing the title (str), date (str), and tags (list of str).
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
    """
    Converts a title to a filename by converting it to a slug and appending '.md'.

    :param title: The title of the note.
    :return: The converted filename as a string.
    """
    return _convert_title_to_slug(title) + ".md"


def _convert_title_to_slug(title: str) -> str:
    """
    Converts a title to a slug by removing special characters, replacing whitespace with hyphens,
    and converting to lowercase.

    :param title: The title of the note.
    :return: The converted slug as a string.
    """
    # Remove special characters, keep only alphanumeric and whitespace
    slug = re.sub(r"[^\w\s-]", "", title)

    # Replace whitespace with "-"
    slug = slug.strip().replace(" ", "-")

    # Replace consecutive "-" with a single "-"
    slug = re.sub(r"[-]+", "-", slug)

    # Convert to lowercase
    slug = slug.lower()

    return slug


def _split_notes(input_lines: list[str]) -> [list[str]]:
    """
    Splits the input lines into separate notes using header lines as delimiters.

    :param input_lines: List of strings, each representing a line from the input.
    :return: A list of notes, where each note is a list of strings.
    """
    # uses "ending" header (ends with "-+") lines as delimiters between notes
    output_notes = []
    note = []
    header_start_found = False
    header_end_found = False
    for line in input_lines:
        if line.endswith("-+") and header_start_found is True and header_end_found is True:
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
            elif line.endswith("-+") and header_start_found is True and header_end_found is False:
                header_end_found = True
            # add line to current temp note
            note.append(line)

    # since we're basing spitting of notes on headers, we won't have a way to store the last note
    # so explicitly store the last note
    output_notes.append(note)
    return output_notes


def _delete_existing_title(note: list[str]) -> list[str]:
    """
    Removes the title line from a markdown note if it starts with "# "
    since the title will already be part of header, and we don't want to display it twice.

    :param note: List of strings, each representing a line from a markdown note.
    :return: List of strings with the title line removed if it starts with "# ".
    """
    if note[0].startswith("# "):
        return note[1:]
    else:
        return note


def _delete_existing_header(note: list[str]) -> list[str]:
    """
    Removes "starting" and "middle" header lines from a note.

    :param note: List of strings, each representing a line from a note.
    :return: List of strings with specific header lines removed.
    """
    output_note = []
    for line in note:
        if not line.startswith("|") and not line.endswith("|") and not line.startswith("+-"):
            output_note.append(line)
    return output_note


def _convert_date_format(input_date: str) -> str:
    """
    Converts a date from the format 'Fri, 01 Sep 2023 02:33:35' to '2018-07-25T03:25:58+00:00'.

    :param input_date: The input date as a string.
    :return: The converted date as a string.
    """
    # convert input_date from format: Fri, 01 Sep 2023 02:33:35
    # to format: 2018-07-25T03:25:58+00:00
    return datetime.strptime(input_date, "%a, %d %b %Y %H:%M:%S").strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )


def _create_ssg_header(
    ssg_type: str, title: str, subtitle: str, author: str, date: str, tags: list[str]
) -> list[str]:
    """
    Creates a static site generator (SSG) header using a template and provided information.

    :param ssg_type: The type of the SSG.
    :param title: The title of the note.
    :param subtitle: The subtitle of the note.
    :param author: The author of the note.
    :param date: The date of the note.
    :param tags: List of tags for the note.
    :return: A list of strings representing the SSG header.
    """
    with open(f"templates/{ssg_type}.md") as template_file:
        template = template_file.read()

    try:
        # Replace template fields with user input
        template = template.replace("{{title}}", title)
        template = template.replace("{{subtitle}}", subtitle)
        template = template.replace("{{author}}", author)
        template = template.replace("{{date}}", date)
        template = template.replace("{{slug}}", _convert_title_to_slug(title))

        # Set unlisted to "true" for any note containing an UNLISTED_TAGS
        for t in UNLISTED_TAGS:
            if t in tags:
                template = template.replace("{{unlisted}}", "true")
            else:
                template = template.replace("{{unlisted}}", "false")

        # don't template the "tag" field with the meta-tag TAG_TO_DOWNLOAD or other IGNORED_TAGS
        for t in IGNORED_TAGS:
            while True:
                try:
                    tags.remove(t)
                except ValueError:
                    break

        if len(tags) == 0:
            template = template.replace("{{tag}}", "Uncategorized")
        if len(tags) == 1:
            template = template.replace("{{tag}}", tags[0])
        elif len(tags) > 1:
            # Join extra tags with new lines prefixed by "  - " and replace the placeholder with the list of tags
            extra_tags_str = "\n".join(f"  - {tag}" for tag in tags[1:])
            tags_str = tags[0] + "\n" + extra_tags_str
            template = template.replace("{{tag}}", tags_str)

        # if we're processing a note with a title-to-summary substitution, set the summary field
        for sub in TITLE_TO_SUMMARY_SUBSTITUTIONS:
            find, replace = sub
            if find in tags:
                # use re.sub() instead of title.replace() since we want to find "find" in "title" regardless of case
                summary_string = re.sub(find, replace, title, flags=re.IGNORECASE)
                template = template.replace("{{summary}}", summary_string)
                break
        # if we don't end up setting a summary, set the summary field to a blank string
        # which causes the summary to not be utilized at all
        template = template.replace("{{summary}}", "")
    except Exception as e:
        print(f"Field missing when templating SSG header: {e}, carrying on!")

    return [x for x in template.split("\n")]


def _prepend_ssg_header(new_header: list[str], note: list[str]) -> list[str]:
    """
    Prepends the SSG header to a note.

    :param new_header: List of strings representing the SSG header.
    :param note: List of strings representing the note.
    :return: A list of strings with the SSG header prepended to the note.
    """
    output_note = []
    for line in new_header + note:
        output_note.append(line + "\n")
    return output_note


def _write_note_file(
    output_markdown: list[str],
    output_dir: str,
    output_filename: str,
):
    """
    Writes the output markdown to a file in the specified directory, checking for existing content to avoid redundant writes.

    :param output_markdown: List of strings representing the markdown content to be written.
    :param output_dir: The directory where the output file will be saved.
    :param output_filename: The name of the output file.
    """
    file_path = f"{output_dir}/{output_filename}"

    # Convert output_markdown to string if it's not already
    if isinstance(output_markdown, list):
        new_content = "".join(output_markdown)

    # Write the resulting markdown to a file if an existing file with the same content doesn't already exist
    try:
        if os.path.exists(file_path):
            with open(file_path) as file:
                existing_content = file.read()

            if existing_content == new_content:
                print(f"The file '{file_path}' already exists with the same content.")
                return
            else:
                print(
                    f"Content differs from the existing file '{file_path}'. Overwriting the file."
                )

        # content differs or file doesn't already exist
        with open(file_path, "w") as output_file:
            output_file.write(new_content)
            print(f"File '{file_path}' has been written successfully.")

    except Exception as e:
        title = "sn2ssg FATAL error"
        message = f"FATAL: error writing note {file_path}: {e}"
        print(message)
        _send_gotify_notification(title, message)
        raise SystemExit


def _ensure_num_parsed_notes_matches_outputted_notes(
    notes_input_counter: int, notes_output_counter: int, start_counter: int, end_counter: int
):
    """
    Ensures the number of parsed notes matches the number of outputted notes, and sends notifications based on the result.

    :param notes_input_counter: The number of parsed notes.
    :param notes_output_counter: The number of outputted notes.
    :param start_counter: The starting counter value.
    :param end_counter: The ending counter value.
    """
    if notes_input_counter != notes_output_counter or end_counter < start_counter:
        title = "sn2ssg FATAL error"
        message = f"FATAL: The number of notes ({notes_input_counter}) does not match the number of outputted files ({notes_output_counter})"
        print(message)
        _send_gotify_notification(title, message)
        sys.exit(1)
    elif os.environ.get("DEBUG") == "True":
        title = "sn2ssg successful"
        message = f"DEBUG: Number of parsed vs outputted notes matches: {notes_input_counter} notes"
        print(message)
        _send_gotify_notification(title, message)
    else:
        message = f"Number of parsed vs outputted notes matches: {notes_input_counter} notes"
        print(message)


def _run_sncli_with_backoff(tag_to_download: str, input_filename: str) -> bool:
    """
    Executes the sncli command to dump notes with a specified tag to a file, with exponential backoff on failures.

    :param tag_to_download: String representing the tag to download notes for.
    :param input_filename: String representing the name of the file to write the output to.
    :return: Boolean indicating success or failure after all retries
    """
    # Define the command as a list of strings
    sncli_binary_path = shutil.which("sncli")

    # If it doesn't exist in the PATH, use the default path
    if sncli_binary_path is None:
        sncli_binary_path = "/home/james/.local/bin/sncli"

    command = [sncli_binary_path, "--config=/dev/null", "-r", "dump", tag_to_download]

    for attempt in range(MAX_RETRIES):
        try:
            print(f"Attempting to dump notes (attempt {attempt + 1}/{MAX_RETRIES})")
            with open(input_filename, "w") as output:
                subprocess.run(command, stdout=output, check=True, text=True)
            print("Dumping of notes via sncli was successful.")
            return True

        except subprocess.CalledProcessError as e:
            print(f"sncli dump attempt {attempt + 1} failed: {e}")

            if attempt < MAX_RETRIES - 1:  # Don't sleep on the last attempt
                delay = _exponential_backoff_delay(attempt)
                print(f"Retrying in {delay:.2f} seconds...")
                time.sleep(delay)
            else:
                print(f"All {MAX_RETRIES} attempts failed for sncli dump")
                return False
        except Exception as e:
            print(f"Unexpected error during sncli dump attempt {attempt + 1}: {e}")

            if attempt < MAX_RETRIES - 1:
                delay = _exponential_backoff_delay(attempt)
                print(f"Retrying in {delay:.2f} seconds...")
                time.sleep(delay)
            else:
                print(f"All {MAX_RETRIES} attempts failed for sncli dump")
                return False

    return False


def _validate_dumped_notes_have_tag_to_download_with_backoff(
    tag_to_download: str, input_filename: str
) -> bool:
    """
    Validates that the dumped notes contain the specified tag, with exponential backoff retry logic.
    If the dumped notes do not, we retry the entire sncli dump and validation process.

    :param tag_to_download: String representing the tag to validate in the dumped notes.
    :param input_filename: String representing the input file to read and validate.
    :return: Boolean indicating whether all the required tags are present in the notes after all retries.
    """
    for attempt in range(MAX_RETRIES):
        try:
            # Read the file content
            with open(input_filename) as input_file:
                input_content = input_file.read()

            input_lines = _trash_sncli_log(input_content)

            # Validate the content
            found_tags_lines = 0
            validation_failed = False

            for line in input_lines:
                if "|" in line and "Tags:" in line:
                    found_tags_lines += 1
                    if tag_to_download not in line:
                        print(
                            f"Did not find required tag {tag_to_download} in note with tags line {line}"
                        )
                        validation_failed = True
                        break

            # Check if we found any tags lines at all
            if found_tags_lines < 1:
                print("Did not find required tags line in raw Simplenote dumped notes")
                validation_failed = True

            if not validation_failed:
                print(
                    f"Validation successful: All {found_tags_lines} notes have the required tag '{tag_to_download}'"
                )
                return True

            # If validation failed and we have more attempts, retry the entire dump process
            if attempt < MAX_RETRIES - 1:
                delay = _exponential_backoff_delay(attempt)
                print(
                    f"Validation failed. Re-dumping notes in {delay:.2f} seconds... (attempt {attempt + 2}/{MAX_RETRIES})"
                )
                time.sleep(delay)

                # Re-run sncli dump
                if not _run_sncli_with_backoff(tag_to_download, input_filename):
                    print(f"Failed to re-dump notes on attempt {attempt + 2}")
                    continue
            else:
                print(f"Validation failed after {MAX_RETRIES} attempts")
                return False

        except FileNotFoundError:
            print(f"Input file {input_filename} not found on attempt {attempt + 1}")
            if attempt < MAX_RETRIES - 1:
                delay = _exponential_backoff_delay(attempt)
                print(f"Retrying dump and validation in {delay:.2f} seconds...")
                time.sleep(delay)

                # Re-run sncli dump
                if not _run_sncli_with_backoff(tag_to_download, input_filename):
                    print(f"Failed to dump notes on attempt {attempt + 2}")
                    continue
            else:
                print(f"File not found after {MAX_RETRIES} attempts")
                return False
        except Exception as e:
            print(f"Unexpected error during validation attempt {attempt + 1}: {e}")
            if attempt < MAX_RETRIES - 1:
                delay = _exponential_backoff_delay(attempt)
                print(f"Retrying in {delay:.2f} seconds...")
                time.sleep(delay)
            else:
                print(f"Validation failed after {MAX_RETRIES} attempts due to errors")
                return False

    return False


def _send_gotify_notification(notification_title: str, notification_text: str):
    """
    Sends a notification using Gotify with the specified title and text, using Gotify config items from environment variables.

    :param notification_title: The title of the notification.
    :param notification_text: The text of the notification.
    """
    if os.environ.get("GOTIFY_URL") and os.environ.get("GOTIFY_TOKEN"):
        url = f"{os.environ.get('GOTIFY_URL')}/message"
    else:
        print("Gotify URL and/or token not specified in environment. No notification was sent")
        return
    params = {"token": os.environ.get("GOTIFY_TOKEN")}
    data = {"title": notification_title, "message": notification_text}
    response = requests.post(url, params=params, data=data)
    print(response.text)


def _get_note_header(note: list[str]) -> list[str]:
    """
    Extracts the header lines from a note.

    :param note: List of strings, each representing a line from a note.
    :return: A list of strings representing the header lines of the note.
    """
    return [
        line for line in note if line.startswith("|") or line.endswith("|") or line.startswith("+-")
    ]


def _adjust_note_header_title(note_header: list[str], title_addition: str) -> list[str]:
    """
    Adjust given note_header by appending title_addition to the end of the found Title field within
    note_header

    :param note_header: note header to be adjusted
    :param title_addition: string to append to end of title within note_header
    :return: adjusted note_header with title addition
    """
    updated_header = []
    for line in note_header:
        match = re.match(SN_HEADER_PATTERN, line)
        if match:
            key = match.group(1).strip()
            value = match.group(2).strip()
            if key == "Title":
                adjusted_title_line = re.sub(value, value + title_addition, line)
                updated_header.append(adjusted_title_line)
            else:
                updated_header.append(line)
        else:
            updated_header.append(line)
    return updated_header


def _remove_tag_from_note_header(note_header: list[str], tag: str, replacement: str) -> list[str]:
    """
    removes tag "tag" from "note_header", replacing it with "replacement"

    :param note_header:
    :param tag: tag to be removed/replaced
    :param replacement: replacement string used in place of old tag

    :return: updated_header is updated copy of note_header with "s/tag/replacement/"
    """
    updated_header = []
    for line in note_header:
        if tag in line:
            updated_tag_line = line.replace(tag, replacement)
            updated_header.append(updated_tag_line)
        else:
            updated_header.append(line)
    return updated_header


def _split_continuous_note(note: list[str]) -> [list[str]]:
    """
    splits up a single continuous note into multiple (one note per line after ssg_header)

    :param note: input continuous note to be split
    :return: list of notes each with same header and one line of continuous note
    """
    notes = []
    cnote_header = _get_note_header(note)
    cnote_header = _remove_tag_from_note_header(
        cnote_header,
        os.environ.get("CONTINUOUS_NOTE_TAG"),
        os.environ.get("CONTINUOUS_NOTE_TAG").split(":")[1],
    )
    cnote_body = note[len(cnote_header) + 1 :]
    cnote_num_parts = len([s for s in cnote_body if s != ""])

    # make a new note each with note_header, but only one line of the input note
    for line in cnote_body:
        # skip empty/blank lines as continuous notes should be blank-line separated
        if line:
            note_tmp = (
                _adjust_note_header_title(cnote_header, f" - {cnote_num_parts}")
                + [""]
                + [line.strip()]
            )
            notes.append(note_tmp)
            cnote_num_parts -= 1
    return notes


def _process_note(note: list[str]) -> int:
    """
    Processes a note by gathering header information, converting the date format, creating an SSG header, and writing the note to a file.

    :param note: List of strings, each representing a line from a note.
    :return: An integer indicating the successful processing of the note.
    """
    title, date, tags = _gather_header_info(SN_HEADER_PATTERN, note)
    note = _delete_existing_header(note)
    subtitle = ""
    author = os.environ.get("AUTHOR", "root")
    date = _convert_date_format(date)
    new_header = _create_ssg_header(os.environ.get("SSG_TYPE"), title, subtitle, author, date, tags)
    note = _delete_existing_title(note)
    note = _prepend_ssg_header(new_header, note)
    _write_note_file(
        note,
        os.environ.get("OUTPUT_DIR"),
        _convert_title_to_filename(title),
    )
    return 1


def main():
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

    # First attempt to dump notes
    if not _run_sncli_with_backoff(os.environ.get("TAG_TO_DOWNLOAD"), input_filename):
        title = "sn2ssg FATAL error"
        message = f"FATAL: Failed to dump notes after {MAX_RETRIES} attempts"
        print(message)
        _send_gotify_notification(title, message)
        sys.exit(1)

    # Validate all raw notes from Simplenote with exponential backoff
    if not _validate_dumped_notes_have_tag_to_download_with_backoff(
        os.environ.get("TAG_TO_DOWNLOAD"), input_filename
    ):
        title = "sn2ssg FATAL error"
        message = f"FATAL: Bailing early since dumped notes from Simplenote don't all have the {os.environ.get('TAG_TO_DOWNLOAD')} tag after {MAX_RETRIES} attempts"
        print(message)
        _send_gotify_notification(title, message)
        sys.exit(1)

    # Read the validated file
    with open(input_filename) as input_file:
        input_lines = input_file.read()
    input_lines = _trash_sncli_log(input_lines)

    # split raw notes and process them
    notes = _split_notes(input_lines)
    notes_input_counter = len(notes)
    notes_output_counter = 0
    for note in notes:
        _, _, tags = _gather_header_info(SN_HEADER_PATTERN, note)
        if os.environ.get("CONTINUOUS_NOTE_TAG") in tags:
            # only split continuous notes (not the one-big-note-of-continuous-notes themselves) get processed...
            split_notes = _split_continuous_note(note)
            notes.extend(split_notes)
            # add the number of split notes minus the now-split-but-not-needing-to-be-processed one-big-note-of-continuous-notes
            notes_input_counter += len(split_notes) - 1
            # forcibly move to next note, otherwise we'll end up processing the one-big-note-of-continuous-notes note as a regular note
            continue
        notes_output_counter += _process_note(note)

    # setup output stats and diff input vs output stats
    output_dir_num_files_end = len(fnmatch.filter(os.listdir(os.environ.get("OUTPUT_DIR")), "*.*"))
    _ensure_num_parsed_notes_matches_outputted_notes(
        notes_input_counter,
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

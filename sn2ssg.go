package main

import (
	"bufio"
	"fmt"
	"log"
	"log/slog"
	"os"
	"os/exec"
	"regexp"
	"strings"
	"time"
)

var (
	SN_HEADER_PATTERN = regexp.MustCompile(`^\|\s*(.*?):\s*(.*?)\s*\|`)
	IGNORED_TAGS      = []string{os.Getenv("TAG_TO_DOWNLOAD"), "blog"}
)

// trashSNCLILog filters out irrelevant log entries from Simplenote CLI output.
func trashSNCLILog(inputLines []string) []string {
	logEntriesToRemove := []string{
		"sncli database doesn't exist",
		"Starting full sync",
		"Synced new note from server",
		"Saved note to disk",
		"Full sync completed",
	}
	var outputLines []string
	for _, line := range inputLines {
		slog.Debug("trashSNCLILog working on line: ", line)
		if !containsAny(line, logEntriesToRemove) {
			outputLines = append(outputLines, line)
		}
	}
	return outputLines
}

// containsAny checks if a string contains any of the substrings in the provided slice.
func containsAny(s string, substrs []string) bool {
	for _, substr := range substrs {
		if strings.Contains(s, substr) {
			return true
		}
	}
	return false
}

// gatherHeaderInfo extracts relevant header information from a note.
func gatherHeaderInfo(pattern *regexp.Regexp, note []string) (string, string, []string) {
	title := ""
	date := ""
	var tags []string
	for _, line := range note {
		match := pattern.FindStringSubmatch(line)
		if len(match) > 0 {
			key := match[1]
			value := match[2]
			switch key {
			case "Title":
				title = strings.ReplaceAll(value, "#", "")
			case "Date":
				date = value
			case "Tags":
				for _, item := range strings.Split(value, ",") {
					tags = append(tags, item)
				}
			}
		}
	}
	return title, date, tags
}

// convertTitleToFilename converts a title to a filename-friendly format.
func convertTitleToFilename(title string) string {
	return convertTitleToSlug(title) + ".md"
}

// convertTitleToSlug converts a title to a slug.
func convertTitleToSlug(title string) string {
	re := regexp.MustCompile(`[^\w\s-]`)
	slug := re.ReplaceAllString(title, "")
	slug = strings.ReplaceAll(slug, " ", "-")
	slug = strings.ReplaceAll(slug, "-+", "-")
	return strings.ToLower(slug)
	// TODO why are slugs starting with "-"?
}

// splitNotes splits input lines into individual notes.
func splitNotes(inputLines []string) [][]string {
	var outputNotes [][]string
	var note []string
	var headerStartFound, headerEndFound bool
	for _, line := range inputLines {
		if strings.HasSuffix(line, "-+") && headerStartFound && headerEndFound {
			outputNotes = append(outputNotes, note)
			note = []string{}
			headerStartFound = true
			headerEndFound = false
			note = append(note, line)
		} else {
			if strings.HasSuffix(line, "-+") && !headerStartFound {
				headerStartFound = true
			} else if strings.HasSuffix(line, "-+") && headerStartFound && !headerEndFound {
				headerEndFound = true
			}
			note = append(note, line)
		}
	}
	outputNotes = append(outputNotes, note)
	return outputNotes
}

// deleteExistingTitle deletes existing title from a note.
func deleteExistingTitle(note []string) []string {
	if strings.HasPrefix(note[0], "# ") {
		return note[1:]
	}
	return note
}

// deleteExistingHeader deletes existing header from a note.
func deleteExistingHeader(note []string) []string {
	var outputNote []string
	for _, line := range note {
		if !strings.HasPrefix(line, "|") && !strings.HasSuffix(line, "|") && !strings.HasPrefix(line, "+-") {
			outputNote = append(outputNote, line)
		}
	}
	return outputNote
}

// convertDateFormat converts a date string to a specific format.
func convertDateFormat(inputDate string) string {
	t, err := time.Parse("Mon, 02 Jan 2006 15:04:05", inputDate)
	if err != nil {
		log.Fatal(err)
	}
	return t.Format("2006-01-02T15:04:05+00:00")
}

// determineTags determines the appropriate tag for a note.
func determineTags(tags []string) string {
	for _, tag := range tags {
		if !contains(IGNORED_TAGS, tag) {
			if tag == os.Getenv("CONTINUOUS_NOTE_TAG") {
				for _, t := range IGNORED_TAGS {
					tag = strings.TrimPrefix(tag, t+":")
				}
			}
			return tag
		}
	}
	return ""
}

// contains checks if a string is present in a slice.
func contains(tags []string, tag string) bool {
	for _, t := range tags {
		if t == tag {
			return true
		}
	}
	return false
}

func main() {
	sncliBinaryPath, err := exec.LookPath("sncli")
	if err != nil {
		sncliBinaryPath = "/home/james/.local/bin/sncli"
	}

	command := []string{sncliBinaryPath, "--config=/dev/null", "-r", "dump", os.Getenv("TAG_TO_DOWNLOAD")}
	inputFilename := os.Getenv("INPUT_DIR") + "/sn_dump.md"

	err = os.WriteFile(inputFilename, []byte{}, 0644)
	if err != nil {
		log.Fatal(err)
	}

	cmd := exec.Command(command[0], command[1:]...)
	outFile, err := os.Create(inputFilename)
	if err != nil {
		log.Fatal(err)
	}
	defer outFile.Close()
	cmd.Stdout = outFile

	err = cmd.Run()
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println("Dumping of notes via sncli was successful.")

	inputBytes, err := os.ReadFile(inputFilename)
	if err != nil {
		log.Fatal(err)
	}
	inputLines := strings.Split(string(inputBytes), "\n")
	slog.Debug("inputLines before trashing: ", inputLines)
	inputLines = trashSNCLILog(inputLines)
	slog.Debug("inputLines after trashing: ", inputLines)

	notes := splitNotes(inputLines)
	slog.Debug("split notes:", notes)
	for _, note := range notes {
		slog.Debug("main working on note: ", note)
		title, date, tags := gatherHeaderInfo(SN_HEADER_PATTERN, note)
		note = deleteExistingHeader(note)
		subtitle := ""
		author := os.Getenv("AUTHOR")
		if author == "" {
			author = "root"
		}
		date = convertDateFormat(date)
		newHeader := createSSGHeader(os.Getenv("SSG_TYPE"), title, subtitle, author, date, tags)
		note = deleteExistingTitle(note)
		note = prependSSGHeader(newHeader, note)
		_ = writeNoteFile(note, os.Getenv("OUTPUT_DIR"), convertTitleToFilename(title))
	}

	timeToSleep, err := time.ParseDuration(os.Getenv("POLLING_CYCLE"))
	if err != nil {
		timeToSleep = 3600 * time.Second
	}

	fmt.Printf("sn2ssg ran successfully! Sleeping %v before next cycle.\n", timeToSleep)
	time.Sleep(timeToSleep)
}

// createSSGHeader creates a header for a static site generator.
func createSSGHeader(ssgType, title, subtitle, author, date string, tags []string) []string {
	template, err := os.ReadFile(fmt.Sprintf("templates/%s.md", ssgType))
	if err != nil {
		log.Fatal(err)
	}
	header := string(template)

	header = strings.ReplaceAll(header, "{{title}}", title)
	header = strings.ReplaceAll(header, "{{subtitle}}", subtitle)
	header = strings.ReplaceAll(header, "{{author}}", author)
	header = strings.ReplaceAll(header, "{{date}}", date)
	header = strings.ReplaceAll(header, "{{slug}}", convertTitleToSlug(title))

	tag := determineTags(tags)
	if tag != "" {
		header = strings.ReplaceAll(header, "{{tag}}", tag)
	} else {
		header = strings.ReplaceAll(header, "{{tag}}", "Uncategorized")
	}

	return strings.Split(header, "\n")
}

// prependSSGHeader prepends a header to a note.
func prependSSGHeader(newHeader, note []string) []string {
	return append(newHeader, note...)
}

// writeNoteFile writes a note to a file.
func writeNoteFile(outputMarkdown []string, outputDir, outputFilename string) error {
	file, err := os.Create(outputDir + "/" + outputFilename)
	if err != nil {
		return err
	}
	defer file.Close()

	writer := bufio.NewWriter(file)
	for _, line := range outputMarkdown {
		_, err := writer.WriteString(line + "\n")
		if err != nil {
			return err
		}
	}
	return writer.Flush()
}

// general TODOs below
// TODO make config work off env vars from .env like ghouls does
// TODO refactor main() to be much smaller and nicer
// TODO refactor code to use custom type struct for note
// TODO refactor code to separate functions for gathering header info to DRY that out
// TODO refactor code to split functionality into separate go files
// utilities.go, note.go, ssg.go, main.go
// TODO add back functionality to count input notes vs outputted notes
// TODO add back functionality to split continuous notes and not process the continuous note itself
// TODO swap log.Fatal() with slog.Error() + os.Exit(1)
// TODO rewrite Docker image to do Go build and install Python sncli library
// TODO adjust Docker Compose projects to use this newly created repo+image, instead of the old, to-be-archived one

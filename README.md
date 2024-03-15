# sn2ssg-py
Convert Simplenote notes to SSG-formatted Markdown files with Python
This can provide a portion of a fully-automated Simplenote note -> SSG-generated website or blogging pipeline

## Usage
### Convert specifically-tagged Simplenote notes to SSG-formatted Markdown
This is the primary use-case of sn2ssg-py, and can either be done via `make` or as part of a docker-compose project (see [docker-compose example](./docker-compose.yml))
1. build sn2ssg-py Docker image
    - `make build`
2. set sn2ssg environment variables
    - `cp .env.sample .env`
    - edit `.env` with correct values for your setup
3. run sn2ssg-py Docker image
    - `make run`

### Output all notes with a specified tag to terminal
Alternatively if you just want to see which notes are tagged with a specific tag, or otherwise download them for another purpose
you can use Docker + sncli package to do so:
1. build sncli Docker image
    - `docker build -t sncli https://github.com/insanum/sncli.git`
2. run sncli
    - `docker run --rm -i -e SN_USERNAME=SIMPLENOTE_USERNAME_GOES_HERE -e SN_PASSWORD=SIMPLENOTE_PASSWORD_GOES_HERE sncli --config=/dev/null -r dump TAG_TO_DOWNLOAD_GOES_HERE`

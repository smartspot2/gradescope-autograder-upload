# Gradescope Autograder Uploader

A utility to bulk upload empty submissions for an assignment on Gradescope. This is particularly helpful if you are setting up an autograder that only requires _some_ student submission to provide information to students.

## Setup

You will need to have a Gradescope account with a password associated with it; if you've only been accessing Gradescope via SSO, you will not be able to log in here to authenticate.

## Uploading submissions

### For all students

To upload submissions for all students in an assignment, run:
```sh
python3 upload.py --all <course_id> <assignment_id>
```
You can retrieve the course ID and assignment ID from the URL of the assignment you want to upload for.

### For a single student

To upload a submission for a single student, run:
```sh
python3 upload.py --email <email> <course_id> <assignment_id>
```

### Options

There are a few options that you can provide as well (help for the script can be retrieved by passing `-h` when executing).

* `--cookies <cookie_file>`: If you've saved your Gradescope authentication cookie in a custom location, you can pass that JSON file here. (Default: `cookies.json`)

* `--threads <count>`: Specify the number of threads to use when sending requests. (Default: 8)

## Checking autograder results

Occasionally, some submissions may time out due to unknown reasons, causing student submissions to fail with a score of 0. A utility script, called `check_autograder_failures.py`, is included in this repository as well to check for any autograder failures and re-run the autograder for those submissions.

To run the script, run the following:
```sh
python3 check_autograder_failures.py <course_id> <assignment_id>
```
This will automatically scrape through all submissions with a score of 0, and check whether the score was due to an error in the autograder. If so, the submission will be regraded. After doing a pass, this script will wait for 1 minute before checking the submissions that were regraded again, looping until all errors are resolved.


## Notes

At the time of writing this script, Gradescope does not implement any request throttling (at least not when using this script in courses of ~800 students and 8 threads), so all requests should go through without error. This means that this script may break if requests start being throttled.

While the requests are being sent, accessing Gradescope in your browser may cause server errors to appear; this is normal.

Additionally, note that these scripts have not been thoroughly tested---they've only been used for attendance tracking for CS70 at UC Berkeley.

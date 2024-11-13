import re
import time

from rich.console import Console
from rich.progress import BarColumn, Progress, TimeElapsedColumn, track

from api.client import GradescopeAPI

GRADESCOPE_BASEURL = "https://www.gradescope.com"
AUTOGRADER_WAIT_SECONDS = 60

CONSOLE = Console(highlight=False)


def validate_and_fix_submission(
    api: GradescopeAPI,
    course_id: int,
    assignment_id: int,
    submission_id: int,
    # for logging purposes
    name: str,
    email: str,
) -> bool:
    """
    Fetch a submission's status, and if the autograder failed,
    submit a POST request to regrade it.

    Returns whether the submission validated.
      - If False, either a request was sent to regrade the submission, or the submission was not finished processing.
      - If True, no further actions were taken, and the submission is validated.
    """
    autograder_output = api.fetch_autograder_submission_status(
        course_id, assignment_id, submission_id
    )

    metadata = autograder_output["metadata"]

    if metadata["status"] == "processed":
        CONSOLE.print(f"[green]processed: {name} ({email})[/green]")
        return True

    if metadata["status"] == "failed":
        CONSOLE.print(f"[red]failed: {name} ({email})[/red]")
    else:
        # other status (likely still regrading, but we should've waited long enough);
        # be conservative and still regrade
        CONSOLE.print(f"[violet]{metadata['status']}: {name} ({email})[/violet]")

    CONSOLE.print("\t[italic red]Regrading submission...[/italic red]")

    (_, csrf_token) = autograder_output["csrf"]

    api.autograder_regrade_submission(
        course_id, assignment_id, submission_id, csrf_token
    )

    return False


def main(course_id: int, assignment_id: int, cookie_file="cookies.json"):
    api = GradescopeAPI(cookie_file=cookie_file)

    grade_data = api.fetch_grades_data(course_id, assignment_id)

    # filter only for submissions that got 0's
    zero_scores = [
        row for row in grade_data if row["score"] is not None and row["score"] == 0
    ]

    submissions_to_validate = []

    i = 0
    for table_row in zero_scores:
        i += 1
        if table_row["submission"] is None:
            # no submission associated
            continue

        match = re.search(
            r"courses/(?P<course_id>\d+)/assignments/(?P<assignment_id>\d+)/submissions/(?P<submission_id>\d+)",
            table_row["submission"],
        )
        assert match is not None, "submission URL is not of the expected format"
        submission_id = match.group("submission_id")
        assert submission_id is not None, "Failed to extract submission id from URL"
        submission_id = int(submission_id)

        submissions_to_validate.append(
            (submission_id, table_row["name"], table_row["email"])
        )

    while len(submissions_to_validate) > 0:
        next_submissions_to_validate = []
        for submission_data in track(
            submissions_to_validate,
            description="Validating submissions...",
            transient=True,
            console=CONSOLE,
        ):
            submission_id, name, email = submission_data
            validated = validate_and_fix_submission(
                api, course_id, assignment_id, submission_id, name, email
            )

            if not validated:
                next_submissions_to_validate.append(submission_data)

        submissions_to_validate = next_submissions_to_validate
        if len(submissions_to_validate) > 0:
            with Progress(
                # columns
                "[progress.description]{task.description}",
                BarColumn(),
                TimeElapsedColumn(),
                # options
                console=CONSOLE,
            ) as progress:
                timer_task = progress.add_task(
                    "[yellow]Waiting 1 minute for submissions to regrade...[/yellow]",
                    total=AUTOGRADER_WAIT_SECONDS,
                )
                for _ in range(AUTOGRADER_WAIT_SECONDS):
                    time.sleep(1)
                    progress.advance(timer_task)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()

    parser.add_argument("course_id", type=int, help="Gradescope course id")
    parser.add_argument("assignment_id", type=int, help="Gradescope assignment id")

    parser.add_argument(
        "--cookies", default="cookies.json", help="Filename for the cookie cache"
    )

    args = parser.parse_args()
    main(args.course_id, args.assignment_id)
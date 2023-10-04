"""
Bulk upload student submissions for a Gradescope assignment.
"""

from concurrent.futures import as_completed
from concurrent.futures.thread import ThreadPoolExecutor
from typing import Union

from rich.console import Console
from rich.progress import Progress

from api.client import GradescopeAPI

CONSOLE = Console(highlight=False)


def main(
    course_id: Union[str, int],
    assignment_id: Union[str, int],
    upload_all=False,
    user_email: str = None,
    cookie_file="cookies.json",
    max_workers: int = 8,
):
    api = GradescopeAPI(cookie_file=cookie_file)

    roster_data, csrf_data = api.fetch_submission_page_data(course_id, assignment_id)
    max_workers = 8

    if upload_all:
        with Progress(transient=True, console=CONSOLE) as progress, ThreadPoolExecutor(
            max_workers
        ) as thread_pool:
            futures = [
                thread_pool.submit(
                    api.upload,
                    course_id,
                    assignment_id,
                    user["id"],
                    csrf_data=csrf_data,
                )
                for user in roster_data
            ]
            upload_task = progress.add_task(
                "Uploading files...", total=len(roster_data)
            )
            for future in as_completed(futures):
                progress.advance(upload_task)
    elif user_email is not None:
        # find the corresponding user id
        user_id = None
        for user in roster_data:
            if user["email"] == user_email:
                user_id = user["id"]
                break

        if user_id is None:
            raise RuntimeError("Failed to find user email in the roster!")

        # upload file
        api.upload(course_id, assignment_id, user_id, csrf_data=csrf_data)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Utility to bulk upload files to a programming assignment on Gradescope."
    )

    parser.add_argument("course_id", type=int, help="Gradescope course id")
    parser.add_argument("assignment_id", type=int, help="Gradescope assignment id")

    parser.add_argument(
        "--cookies", default="cookies.json", help="Filename for the cookie cache"
    )
    parser.add_argument(
        "--all", action="store_true", help="Upload a file for all students"
    )
    parser.add_argument("--email", help="Email of user to upload a file for")
    parser.add_argument(
        "--threads",
        type=int,
        default=8,
        help="Maximum number of threads to use for upload requests.",
    )

    args = parser.parse_args()
    main(
        course_id=args.course_id,
        assignment_id=args.assignment_id,
        upload_all=args.all,
        cookie_file=args.cookies,
        user_email=args.email,
        max_workers=args.threads,
    )

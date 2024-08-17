import logging
from git import Repo
from dotenv import load_dotenv
import os

load_dotenv()

GITHUB_USER = os.getenv("GITHUB_USER")
GITHUB_PAT = os.getenv("GITHUB_PAT")


class GitHubUpdater:
    def push_changes(self):
        repo = Repo(".")
        repo.git.add("static/historical_data.geojson")

        if repo.is_dirty(untracked_files=True):
            repo.git.commit("-m", "Updated historical data")
            auth_string = f"{GITHUB_USER}:{GITHUB_PAT}"
            repo.git.push(
                "https://" + auth_string + "@github.com/realronaldrump/every-street.git",
                "main",
            )
            logging.info("Changes committed and pushed to GitHub.")
        else:
            logging.info("No changes to commit. The repository is clean.")
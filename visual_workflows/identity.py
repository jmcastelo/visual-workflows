"""The program's names, in one place.

Visual Workflows is a working name: the suite it belongs to (with Visual
Excitons) may be renamed. Everything user-visible that carries the name is
defined here, so a rename touches this module and the package name only.

Files shared between people (cluster profiles) never carry the program's
name: they identify themselves by FORMAT keys, so files sent to colleagues
keep loading after a rename.
"""

APP_NAME = "Visual Workflows"
# QSettings organisation and application: renaming them loses the user's
# settings unless they are migrated, so a rename must copy the old ones
ORG_NAME = "visual-suite"
SETTINGS_NAME = "visual-workflows"
# Service name under which passwords and key passphrases go in the OS keyring
KEYRING_SERVICE = "visual-workflows"

# File formats: "<kind>/<version>" in each file's top-level `format` key
FORMAT_CLUSTER_PROFILE = "cluster-profile/1"
FORMAT_PROJECT = "workflow-project/1"

# File name suffixes
CLUSTER_PROFILE_SUFFIX = ".cluster.toml"
PROJECT_FILE = "project.toml"

# Default folder for projects on a cluster, written into each new cluster
# profile (so existing profiles keep their folder after a rename)
DEFAULT_REMOTE_WORK_ROOT = "$HOME/visual-workflows"

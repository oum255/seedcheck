# Seedcheck

Seedcheck is a command-line tool that answers one question: *does this
cloud-init configuration work on every Ubuntu release I care about?* Given a
cloud-init user-data file and a list of expectations, it validates the file,
launches one LXD container per Ubuntu release (22.04, 24.04, 26.04), waits for
cloud-init to finish, runs checks inside each container (packages, services,
files, users, commands and cloud-init's own error report), and prints a
pass/fail matrix per release. Work in progress.

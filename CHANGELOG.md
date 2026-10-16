# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Pressing Ctrl+C while a container was being launched could leave the
  container behind: LXD keeps creating it server-side and refuses to delete
  a "busy" instance. Deletion now waits for that operation to end.
  (Found by testing interruption against a real LXD; covered by
  `test_delete_waits_for_an_interrupted_launch_to_finish`.)
- The Debian package installed the examples as a stray top-level
  `examples` module in `/usr/lib/python3/dist-packages`, because pybuild
  installs whatever the unit tests leave in its build directory. They are
  now removed after the tests and shipped in
  `/usr/share/doc/seedcheck/examples/` instead, and `scripts/build-deb.sh`
  fails if anything but `seedcheck` lands in `dist-packages`. (Found by
  reading `dpkg -c`; lintian did not report it.)

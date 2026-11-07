# Future bug patches

No patches are present or applied. After explicit authorization to introduce
bugs, save each intentional change here as a separate numbered patch matching
the catalog and checklist. These files stay outside worker containers.

The prepared source is in the ignored `../workspace/source/` directory. The
current build command refuses modified source: it is only for the unchanged
starting point. Bug introduction will use a new versioned image and manifest
listing exactly which bug IDs were applied; it must not replace the baseline
or `experiment-ready-v1` image.

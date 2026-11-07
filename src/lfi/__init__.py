"""Local File Intelligence: local-first file search, duplicates, cleanup and organization."""

import os

# NumPy's bundled OpenBLAS commits a work buffer for every CPU thread when it is imported: about
# 500 MB of private memory on a 16-thread laptop, in every process that imports NumPy. The app's
# matrix work is small, so cap it before anything imports NumPy (a few threads still speed up the
# similar-document scan). A value the user set explicitly is kept.
BLAS_THREADS_DEFAULTED = "OPENBLAS_NUM_THREADS" not in os.environ
os.environ.setdefault("OPENBLAS_NUM_THREADS", "4")

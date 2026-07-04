"""Throwaway scratch module for the #583 CI-red exercise. DO NOT MERGE.

Named without a ``test_`` prefix so pytest does not collect it; ``ruff check tests/``
still lints it. The unused import below is a deliberate F401 to trip the CI Lint step.
"""

import os  # deliberate F401 for the #583 CI-red exercise

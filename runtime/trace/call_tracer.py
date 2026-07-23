"""Record which call edges actually fire, via sys.setprofile.

The static graph resolves ~12% of CALLS edges; the rest are unbound bare
names. A runtime trace tells us two things the static graph can't: which
resolved edges never fire (candidate phantoms), and which fired edges the
resolver never found (missed edges). We key everything by
``co_filename::co_qualname`` so it lines up with graph qualified_names
(``/abs/file.py::Class.method``) on Python 3.11+.
"""

from __future__ import annotations

import sys
import threading


class CallTracer:
    def __init__(self, pkg_prefix: str):
        self.pkg = pkg_prefix
        self.edges: set[tuple[str, str]] = set()   # (caller_qual, callee_qual)
        self.fired: set[str] = set()               # callee_qual, package only

    def _profile(self, frame, event, arg):
        if event != "call":
            return
        code = frame.f_code
        fn = code.co_filename
        if not fn.startswith(self.pkg):
            return
        callee = f"{fn}::{code.co_qualname}"
        self.fired.add(callee)
        back = frame.f_back
        if back is not None:
            bc = back.f_code
            self.edges.add((f"{bc.co_filename}::{bc.co_qualname}", callee))

    def start(self) -> None:
        sys.setprofile(self._profile)
        threading.setprofile(self._profile)

    def stop(self) -> None:
        sys.setprofile(None)
        threading.setprofile(None)

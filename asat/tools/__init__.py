"""Developer-facing CLI tools that ship inside the asat package.

These modules are not part of the runtime — `python -m asat` does not
import any of them. They exist so a developer can regenerate
documentation, dump diagnostic snapshots, or inspect internals without
hand-rolling shell incantations.
"""

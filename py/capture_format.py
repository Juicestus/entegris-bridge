"""
How a profile capture is laid out on disk, shared by the script that writes it and the one that reads it. Imports
nothing from the project, so the reader can run with only this, the decoder and a capture directory.
"""

INFO_FILE_NAME = "profile_info.json"
WORD_LEN = 2


def trace_file_name(trace):
    return f"trace_{trace}.bin"


def bad_file_name(trace, start):
    """
    Where a reply that failed its checks is kept. Never read back as trace data, it's only for diagnosis.
    """
    return f"trace_{trace}_{start}.bad"

"""Wire format for the EO link (Pi -> laptop).

One TCP stream, one JPEG per frame, fixed-size header in front of each.
Deliberately not RTSP/WebRTC: those hide buffering inside the stack, and the
pitch quotes a latency number we have to be able to defend.

Header (24 bytes, little-endian, packed):
    magic     4s   b"HKV1"
    seq       I    frame counter, monotonic from sender start
    cap_ts    d    sender's time.monotonic() at capture -- SENDER CLOCK ONLY
    width     H
    height    H
    nbytes    I    JPEG payload length that follows

cap_ts is on the sender's clock and the two clocks are NOT synchronised, so
receiver_now - cap_ts is meaningless. It is used only for sender-side pipeline
timing (capture -> encode -> handoff). True glass-to-display latency is measured
with the screen-timer procedure in docs/eo_bringup.md.
"""

import struct

MAGIC = b"HKV1"
HEADER = struct.Struct("<4sIdHHI")
HEADER_SIZE = HEADER.size  # 24


def pack_header(seq: int, cap_ts: float, width: int, height: int, nbytes: int) -> bytes:
    return HEADER.pack(MAGIC, seq & 0xFFFFFFFF, cap_ts, width, height, nbytes)


def unpack_header(buf: bytes):
    magic, seq, cap_ts, width, height, nbytes = HEADER.unpack(buf)
    if magic != MAGIC:
        raise ValueError(f"bad magic {magic!r} -- stream desynchronised")
    return seq, cap_ts, width, height, nbytes


def recv_exactly(sock, n: int) -> bytes:
    """Read exactly n bytes or raise ConnectionError. socket.recv is short-read
    prone on a saturated USB-gadget link; this is where naive code corrupts."""
    chunks = []
    remaining = n
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("peer closed mid-frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)

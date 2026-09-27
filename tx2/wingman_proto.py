"""Wingman wire protocol, Python mirror of protocol/wingman_proto.h.

Python 3.6 compatible (runs on the TX2 as-is). Little-endian, packed.
    perception  TX2 -> RT1170  UDP :5005  146 bytes, always full size
    status      RT1170 -> TX2  UDP :5006   36 bytes
CRC-16/CCITT-FALSE over every byte before the crc field == binascii.crc_hqx(data, 0xFFFF).
"""
import binascii
import collections
import struct

MAGIC_PERCEPTION = 0x574D  # "WM"
MAGIC_STATUS = 0x5753      # "WS"
VERSION = 1
MAX_TRACKS = 8

PORT_PERCEPTION = 5005
PORT_STATUS = 5006

CLS_UNKNOWN, CLS_PERSON, CLS_CAR, CLS_BIKE, CLS_TRUCK = range(5)

BOOT, WAIT_LINK, NOMINAL, CAUTION, WARNING, FAILSAFE = range(6)
STATE_NAMES = ["BOOT", "WAIT_LINK", "NOMINAL", "CAUTION", "WARNING", "FAILSAFE"]

MANEUVER_NAMES = ["unknown", "steady", "brake", "accel", "swerve"]

FAULT_HEARTBEAT = 1 << 0
FAULT_CRC = 1 << 1
FAULT_SEQ_GAP = 1 << 2
FAULT_SEQ_OLD = 1 << 3
FAULT_BAD_LEN = 1 << 4
FAULT_WDT_RESET = 1 << 5
FAULT_NAMES = [(FAULT_HEARTBEAT, "HEARTBEAT"), (FAULT_CRC, "CRC"), (FAULT_SEQ_GAP, "SEQ_GAP"),
               (FAULT_SEQ_OLD, "SEQ_OLD"), (FAULT_BAD_LEN, "BAD_LEN"),
               (FAULT_WDT_RESET, "WDT_RESET")]

TTC_NONE = -1
THREAT_NONE = 0xFFFF

_TRACK = "HBBiii"
_PERCEPTION_HDR = "<HBBIQ"
PERCEPTION_FMT = _PERCEPTION_HDR + _TRACK * MAX_TRACKS + "H"
STATUS_FMT = "<HBBIQiHHIhhBBH"

PERCEPTION_SIZE = struct.calcsize(PERCEPTION_FMT)
STATUS_SIZE = struct.calcsize(STATUS_FMT)
assert struct.calcsize("<" + _TRACK) == 16
assert PERCEPTION_SIZE == 146, PERCEPTION_SIZE
assert STATUS_SIZE == 36, STATUS_SIZE

Track = collections.namedtuple("Track", "id cls conf dist_mm lat_mm closing_mm_s")
Status = collections.namedtuple(
    "Status",
    "magic version state seq_echo tx_time_us_echo ttc_ms threat_id fault_flags "
    "decision_latency_ns imu_ax_mg imu_ay_mg maneuver reserved crc16")


def crc16(data):
    return binascii.crc_hqx(bytes(data), 0xFFFF)


assert crc16(b"123456789") == 0x29B1


def _clamp_i32(v):
    return max(-2147483648, min(2147483647, int(round(v))))


def pack_perception(seq, tx_time_us, tracks):
    """tracks: iterable of Track (at most 8). Always packs the full 146 bytes."""
    tracks = list(tracks)[:MAX_TRACKS]
    fields = [MAGIC_PERCEPTION, VERSION, len(tracks), seq & 0xFFFFFFFF,
              tx_time_us & 0xFFFFFFFFFFFFFFFF]
    for i in range(MAX_TRACKS):
        if i < len(tracks):
            t = tracks[i]
            fields += [t.id & 0xFFFF, t.cls & 0xFF, max(0, min(255, int(t.conf))),
                       _clamp_i32(t.dist_mm), _clamp_i32(t.lat_mm), _clamp_i32(t.closing_mm_s)]
        else:
            fields += [0, 0, 0, 0, 0, 0]
    body = struct.pack(PERCEPTION_FMT[:-1], *fields)
    return body + struct.pack("<H", crc16(body))


def unpack_perception(data):
    """Returns (seq, tx_time_us, [Track]) or raises ValueError."""
    if len(data) != PERCEPTION_SIZE:
        raise ValueError("bad length %d" % len(data))
    v = struct.unpack(PERCEPTION_FMT, data)
    if crc16(data[:-2]) != v[-1]:
        raise ValueError("bad crc")
    magic, version, n, seq, tx = v[:5]
    if magic != MAGIC_PERCEPTION or version != VERSION or n > MAX_TRACKS:
        raise ValueError("bad header")
    tracks = [Track(*v[5 + 6 * i:11 + 6 * i]) for i in range(n)]
    return seq, tx, tracks


def pack_status(state, seq_echo, tx_time_us_echo, ttc_ms=TTC_NONE, threat_id=THREAT_NONE,
                fault_flags=0, decision_latency_ns=0, imu_ax_mg=0, imu_ay_mg=0, maneuver=0):
    body = struct.pack(STATUS_FMT[:-1], MAGIC_STATUS, VERSION, state, seq_echo, tx_time_us_echo,
                       ttc_ms, threat_id, fault_flags, decision_latency_ns, imu_ax_mg,
                       imu_ay_mg, maneuver, 0)
    return body + struct.pack("<H", crc16(body))


def unpack_status(data):
    """Returns Status or None if the packet is not a valid status packet."""
    if len(data) != STATUS_SIZE or crc16(data[:-2]) != struct.unpack_from("<H", data, 34)[0]:
        return None
    s = Status(*struct.unpack(STATUS_FMT, data))
    if s.magic != MAGIC_STATUS or s.version != VERSION:
        return None
    return s


def fault_str(flags):
    names = [n for bit, n in FAULT_NAMES if flags & bit]
    return "|".join(names) if names else "-"


def state_name(s):
    return STATE_NAMES[s] if 0 <= s < len(STATE_NAMES) else "?%d" % s

"""
LED-matrix load gauge — App Lab port of ``src/ingestion/led_matrix.py``.

Renders CPU and memory utilisation as two bottom-up bars on the UNO Q's
onboard 12x8 LED matrix (left half = CPU %, right half = memory %). In App Lab
the matrix is owned by the MCU sketch, so this module only computes the frame
and packs it into the 3x uint32 layout the sketch's ``set_matrix`` RPC expects;
``main.py`` pushes it over the Bridge.

Pure functions here (no hardware, no psutil) so they stay unit-testable on a
dev machine — mirrors how the original module kept a simulated backend.
"""

MATRIX_COLS = 12
MATRIX_ROWS = 8
_BAR_COLS = MATRIX_COLS // 2

Frame = list[list[bool]]


def _bar_height(pct: float, rows: int = MATRIX_ROWS) -> int:
    """Convert a 0-100 percentage into a 0..rows lit-row count."""
    return round(max(0.0, min(100.0, pct)) / 100.0 * rows)


def render_frame(cpu_pct: float, mem_pct: float) -> Frame:
    """Build an 8x12 grid: left half = CPU bar, right half = memory bar."""
    cpu_h = _bar_height(cpu_pct)
    mem_h = _bar_height(mem_pct)
    frame = [[False] * MATRIX_COLS for _ in range(MATRIX_ROWS)]
    for row in range(MATRIX_ROWS):
        lit_from_bottom = MATRIX_ROWS - row
        if lit_from_bottom <= cpu_h:
            for col in range(_BAR_COLS):
                frame[row][col] = True
        if lit_from_bottom <= mem_h:
            for col in range(_BAR_COLS, MATRIX_COLS):
                frame[row][col] = True
    return frame


def pack_frame(frame: Frame) -> tuple[int, int, int, int, int, int]:
    """Pack an 8x12 bool grid into 6x uint16 (3 words split hi/lo) for Bridge.call.

    Pixel n = row*12 + col, n=0 (top-left) is the MSB of word 0 — the layout
    ``Arduino_LED_Matrix.loadFrame(const uint32_t[3])`` expects on the R4-style
    matrix. Confirm the bit order against your UNO Q's matrix library.

    RouterBridge infers parameter type from the Python int value, so uint32_t
    arguments get mis-encoded when the value fits in a smaller type. Each 32-bit
    word is split into a high uint16 and low uint16; the MCU sketch's
    ``set_matrix`` RPC reconstructs the uint32_t values before calling loadFrame.
    """
    words = [0, 0, 0]
    for row in range(MATRIX_ROWS):
        for col in range(MATRIX_COLS):
            if not frame[row][col]:
                continue
            n = row * MATRIX_COLS + col
            words[n // 32] |= 1 << (31 - (n % 32))
    return (
        (words[0] >> 16) & 0xFFFF, words[0] & 0xFFFF,
        (words[1] >> 16) & 0xFFFF, words[1] & 0xFFFF,
        (words[2] >> 16) & 0xFFFF, words[2] & 0xFFFF,
    )


def gauge_words(cpu_pct: float, mem_pct: float) -> tuple[int, int, int, int, int, int]:
    """CPU/memory percentages -> 6x uint16 (hi/lo pairs) ready for Bridge.call."""
    return pack_frame(render_frame(cpu_pct, mem_pct))

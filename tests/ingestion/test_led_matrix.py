from src.ingestion.led_matrix import (
    MATRIX_COLS,
    MATRIX_ROWS,
    _BAR_COLS,
    _bar_height,
    render_frame,
)


def test_bar_height_clamps_to_range():
    assert _bar_height(-10.0) == 0
    assert _bar_height(0.0) == 0
    assert _bar_height(100.0) == MATRIX_ROWS
    assert _bar_height(150.0) == MATRIX_ROWS


def test_bar_height_scales_linearly():
    assert _bar_height(50.0) == round(0.5 * MATRIX_ROWS)


def test_render_frame_shape():
    frame = render_frame(0.0, 0.0)
    assert len(frame) == MATRIX_ROWS
    assert all(len(row) == MATRIX_COLS for row in frame)


def test_render_frame_zero_load_is_dark():
    frame = render_frame(0.0, 0.0)
    assert not any(any(row) for row in frame)


def test_render_frame_full_load_lights_everything():
    frame = render_frame(100.0, 100.0)
    assert all(all(row) for row in frame)


def test_render_frame_splits_cpu_and_memory_columns():
    # High CPU, idle memory: only the left bar (cols 0.._BAR_COLS) should light up.
    frame = render_frame(100.0, 0.0)
    bottom_row = frame[MATRIX_ROWS - 1]
    assert all(bottom_row[:_BAR_COLS])
    assert not any(bottom_row[_BAR_COLS:])


def test_render_frame_fills_bottom_up():
    # Half load lights the bottom half of the bar, top half stays dark.
    frame = render_frame(50.0, 50.0)
    half = MATRIX_ROWS // 2
    assert not any(any(row) for row in frame[:half])
    assert all(any(row) for row in frame[half:])

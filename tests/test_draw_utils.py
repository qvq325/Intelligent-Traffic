import numpy as np

import draw_utils


def test_vehicle_box_routes_cjk_label_through_pillow(monkeypatch):
    frame = np.zeros((80, 160, 3), dtype=np.uint8)
    calls = []

    monkeypatch.setattr(draw_utils, "HAS_PIL", True)
    monkeypatch.setattr(
        draw_utils,
        "_draw_text_pil",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        draw_utils.cv2,
        "putText",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("CJK labels must not use OpenCV text rendering")
        ),
    )

    label = "\u5c0f\u6c7d\u8f66 90%"
    draw_utils.draw_vehicle_box(
        frame,
        (10, 30, 110, 70),
        label,
        draw_utils.COLOR_BLUE,
    )

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] is frame
    assert args[1] == label
    assert args[3] == draw_utils.COLOR_BLUE
    assert kwargs == {"font_size": 16, "padding": 2}

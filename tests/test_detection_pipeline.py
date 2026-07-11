import torch

from detection_processor import DetectionProcessor
from lpr_recognizer import LPRRecognizer, PLATE_CHARACTERS, PlateRecognition


def test_ctc_decode_collapses_blanks_and_repeated_characters():
    character_indices = [0, 1, 1, 0, 50, 50, 0]
    logits = torch.full((len(character_indices), len(PLATE_CHARACTERS)), -20.0)
    for position, character_index in enumerate(character_indices):
        logits[position, character_index] = 20.0

    text, confidence = LPRRecognizer._decode(logits)

    assert text == PLATE_CHARACTERS[1] + PLATE_CHARACTERS[50]
    assert confidence > 0.99


def test_plate_is_associated_by_center_point():
    inside = PlateRecognition("京A12345", 0.9, (40, 50, 80, 70))
    outside = PlateRecognition("京A54321", 0.9, (110, 50, 130, 70))
    vehicle_bbox = (10, 10, 100, 100)

    assert DetectionProcessor._plate_belongs_to_vehicle(inside, vehicle_bbox)
    assert not DetectionProcessor._plate_belongs_to_vehicle(outside, vehicle_bbox)

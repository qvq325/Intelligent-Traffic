"""CRNN network used by we0091234's Chinese plate recognizer.

Architecture source:
https://github.com/we0091234/yolo26-plate/blob/main/plate_recognition/plateNet.py
"""

from __future__ import annotations

from typing import Sequence, Union

import torch
from torch import nn


LayerConfig = Sequence[Union[int, str]]


class PlateOCRNet(nn.Module):
    """Character and plate-color recognition network."""

    def __init__(
        self,
        cfg: LayerConfig,
        num_classes: int,
        color_classes: int = 5,
    ) -> None:
        super().__init__()
        self.feature = self._make_layers(cfg)
        out_channels = int(cfg[-1])

        self.color_conv = nn.Conv2d(out_channels, 12, kernel_size=3, stride=2)
        self.color_bn = nn.BatchNorm2d(12)
        self.color_relu = nn.ReLU(inplace=True)
        self.color_classifier = nn.Conv2d(12, color_classes, kernel_size=1)
        self.color_classifier_bn = nn.BatchNorm2d(color_classes)
        self.color_pool = nn.AdaptiveAvgPool2d(output_size=1)
        self.color_flatten = nn.Flatten()

        self.sequence_pool = nn.MaxPool2d((5, 2), (1, 1), (0, 1))
        self.character_classifier = nn.Conv2d(out_channels, num_classes, kernel_size=1)

    @staticmethod
    def _make_layers(cfg: LayerConfig) -> nn.Sequential:
        layers = []
        in_channels = 3
        for index, value in enumerate(cfg):
            if value == "M":
                layers.append(nn.MaxPool2d(kernel_size=3, stride=2, ceil_mode=True))
                continue

            out_channels = int(value)
            kernel_size = 5 if index == 0 else 3
            padding = 0 if index == 0 else 1
            layers.extend(
                [
                    nn.Conv2d(in_channels, out_channels, kernel_size, padding=padding),
                    nn.BatchNorm2d(out_channels),
                    nn.ReLU(inplace=True),
                ]
            )
            in_channels = out_channels
        return nn.Sequential(*layers)

    def forward(self, tensor: torch.Tensor):
        features = self.feature(tensor)

        colors = self.color_conv(features)
        colors = self.color_bn(colors)
        colors = self.color_relu(colors)
        colors = self.color_classifier(colors)
        colors = self.color_classifier_bn(colors)
        colors = self.color_pool(colors)
        colors = self.color_flatten(colors)

        characters = self.sequence_pool(features)
        characters = self.character_classifier(characters)
        characters = characters.squeeze(2).transpose(2, 1)
        return characters, colors

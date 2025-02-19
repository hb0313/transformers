# Copyright 2021 The HuggingFace Team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import hashlib
import unittest
from typing import Dict

import datasets
import numpy as np
from datasets import load_dataset

from transformers import (
    MODEL_FOR_IMAGE_SEGMENTATION_MAPPING,
    MODEL_FOR_INSTANCE_SEGMENTATION_MAPPING,
    MODEL_FOR_SEMANTIC_SEGMENTATION_MAPPING,
    AutoFeatureExtractor,
    AutoModelForImageSegmentation,
    AutoModelForInstanceSegmentation,
    DetrForSegmentation,
    ImageSegmentationPipeline,
    MaskFormerForInstanceSegmentation,
    is_vision_available,
    pipeline,
)
from transformers.testing_utils import nested_simplify, require_tf, require_timm, require_torch, require_vision, slow

from .test_pipelines_common import ANY, PipelineTestCaseMeta


if is_vision_available():
    from PIL import Image
else:

    class Image:
        @staticmethod
        def open(*args, **kwargs):
            pass


def hashimage(image: Image) -> str:
    m = hashlib.md5(image.tobytes())
    return m.hexdigest()[:10]


def mask_to_test_readable(mask: Image) -> Dict:
    npimg = np.array(mask)
    white_pixels = (npimg == 255).sum()
    shape = npimg.shape
    return {"hash": hashimage(mask), "white_pixels": white_pixels, "shape": shape}


@require_vision
@require_timm
@require_torch
class ImageSegmentationPipelineTests(unittest.TestCase, metaclass=PipelineTestCaseMeta):
    model_mapping = {
        k: v
        for k, v in (
            list(MODEL_FOR_IMAGE_SEGMENTATION_MAPPING.items()) if MODEL_FOR_IMAGE_SEGMENTATION_MAPPING else []
        )
        + (MODEL_FOR_SEMANTIC_SEGMENTATION_MAPPING.items() if MODEL_FOR_SEMANTIC_SEGMENTATION_MAPPING else [])
        + (MODEL_FOR_INSTANCE_SEGMENTATION_MAPPING.items() if MODEL_FOR_INSTANCE_SEGMENTATION_MAPPING else [])
    }

    def get_test_pipeline(self, model, tokenizer, feature_extractor):
        image_segmenter = ImageSegmentationPipeline(model=model, feature_extractor=feature_extractor)
        return image_segmenter, [
            "./tests/fixtures/tests_samples/COCO/000000039769.png",
            "./tests/fixtures/tests_samples/COCO/000000039769.png",
        ]

    def run_pipeline_test(self, image_segmenter, examples):
        outputs = image_segmenter("./tests/fixtures/tests_samples/COCO/000000039769.png", threshold=0.0)
        self.assertIsInstance(outputs, list)
        n = len(outputs)
        if isinstance(image_segmenter.model, (MaskFormerForInstanceSegmentation)):
            # Instance segmentation (maskformer) have a slot for null class
            # and can output nothing even with a low threshold
            self.assertGreaterEqual(n, 0)
        else:
            self.assertGreaterEqual(n, 1)
        # XXX: PIL.Image implements __eq__ which bypasses ANY, so we inverse the comparison
        # to make it work
        self.assertEqual([{"score": ANY(float, type(None)), "label": ANY(str), "mask": ANY(Image.Image)}] * n, outputs)

        dataset = datasets.load_dataset("hf-internal-testing/fixtures_image_utils", "image", split="test")

        # RGBA
        outputs = image_segmenter(dataset[0]["file"])
        m = len(outputs)
        self.assertEqual([{"score": ANY(float, type(None)), "label": ANY(str), "mask": ANY(Image.Image)}] * m, outputs)
        # LA
        outputs = image_segmenter(dataset[1]["file"])
        m = len(outputs)
        self.assertEqual([{"score": ANY(float, type(None)), "label": ANY(str), "mask": ANY(Image.Image)}] * m, outputs)
        # L
        outputs = image_segmenter(dataset[2]["file"])
        m = len(outputs)
        self.assertEqual([{"score": ANY(float, type(None)), "label": ANY(str), "mask": ANY(Image.Image)}] * m, outputs)

        if isinstance(image_segmenter.model, DetrForSegmentation):
            # We need to test batch_size with images with the same size.
            # Detr doesn't normalize the size of the images, meaning we can have
            # 800x800 or 800x1200, meaning we cannot batch simply.
            # We simply bail on this
            batch_size = 1
        else:
            batch_size = 2

        # 5 times the same image so the output shape is predictable
        batch = [
            "./tests/fixtures/tests_samples/COCO/000000039769.png",
            "./tests/fixtures/tests_samples/COCO/000000039769.png",
            "./tests/fixtures/tests_samples/COCO/000000039769.png",
            "./tests/fixtures/tests_samples/COCO/000000039769.png",
            "./tests/fixtures/tests_samples/COCO/000000039769.png",
        ]
        outputs = image_segmenter(batch, threshold=0.0, batch_size=batch_size)
        self.assertEqual(len(batch), len(outputs))
        self.assertEqual(len(outputs[0]), n)
        self.assertEqual(
            [
                [{"score": ANY(float, type(None)), "label": ANY(str), "mask": ANY(Image.Image)}] * n,
                [{"score": ANY(float, type(None)), "label": ANY(str), "mask": ANY(Image.Image)}] * n,
                [{"score": ANY(float, type(None)), "label": ANY(str), "mask": ANY(Image.Image)}] * n,
                [{"score": ANY(float, type(None)), "label": ANY(str), "mask": ANY(Image.Image)}] * n,
                [{"score": ANY(float, type(None)), "label": ANY(str), "mask": ANY(Image.Image)}] * n,
            ],
            outputs,
            f"Expected [{n}, {n}, {n}, {n}, {n}], got {[len(item) for item in outputs]}",
        )

    @require_tf
    @unittest.skip("Image segmentation not implemented in TF")
    def test_small_model_tf(self):
        pass

    @require_torch
    def test_small_model_pt(self):
        model_id = "hf-internal-testing/tiny-detr-mobilenetsv3-panoptic"

        model = AutoModelForImageSegmentation.from_pretrained(model_id)
        feature_extractor = AutoFeatureExtractor.from_pretrained(model_id)
        image_segmenter = ImageSegmentationPipeline(
            model=model,
            feature_extractor=feature_extractor,
            task="semantic",
            threshold=0.0,
            overlap_mask_area_threshold=0.0,
        )

        outputs = image_segmenter(
            "http://images.cocodataset.org/val2017/000000039769.jpg",
        )

        # Shortening by hashing
        for o in outputs:
            o["mask"] = mask_to_test_readable(o["mask"])

        # This is extremely brittle, and those values are made specific for the CI.
        self.assertEqual(
            nested_simplify(outputs, decimals=4),
            [
                {
                    "label": "LABEL_88",
                    "mask": {"hash": "7f0bf661a4", "shape": (480, 640), "white_pixels": 3},
                    "score": None,
                },
                {
                    "label": "LABEL_101",
                    "mask": {"hash": "10ab738dc9", "shape": (480, 640), "white_pixels": 8948},
                    "score": None,
                },
                {
                    "label": "LABEL_215",
                    "mask": {"hash": "b431e0946c", "shape": (480, 640), "white_pixels": 298249},
                    "score": None,
                },
            ]
            # Temporary: Keeping around the old values as they might provide useful later
            # [
            #     {
            #         "score": 0.004,
            #         "label": "LABEL_215",
            #         "mask": {"hash": "34eecd16bb", "shape": (480, 640), "white_pixels": 0},
            #     },
            #     {
            #         "score": 0.004,
            #         "label": "LABEL_215",
            #         "mask": {"hash": "34eecd16bb", "shape": (480, 640), "white_pixels": 0},
            #     },
            # ],
        )

        outputs = image_segmenter(
            [
                "http://images.cocodataset.org/val2017/000000039769.jpg",
                "http://images.cocodataset.org/val2017/000000039769.jpg",
            ],
            threshold=0.0,
        )
        for output in outputs:
            for o in output:
                o["mask"] = mask_to_test_readable(o["mask"])

        self.assertEqual(
            nested_simplify(outputs, decimals=4),
            [
                [
                    {
                        "label": "LABEL_88",
                        "mask": {"hash": "7f0bf661a4", "shape": (480, 640), "white_pixels": 3},
                        "score": None,
                    },
                    {
                        "label": "LABEL_101",
                        "mask": {"hash": "10ab738dc9", "shape": (480, 640), "white_pixels": 8948},
                        "score": None,
                    },
                    {
                        "label": "LABEL_215",
                        "mask": {"hash": "b431e0946c", "shape": (480, 640), "white_pixels": 298249},
                        "score": None,
                    },
                ],
                [
                    {
                        "label": "LABEL_88",
                        "mask": {"hash": "7f0bf661a4", "shape": (480, 640), "white_pixels": 3},
                        "score": None,
                    },
                    {
                        "label": "LABEL_101",
                        "mask": {"hash": "10ab738dc9", "shape": (480, 640), "white_pixels": 8948},
                        "score": None,
                    },
                    {
                        "label": "LABEL_215",
                        "mask": {"hash": "b431e0946c", "shape": (480, 640), "white_pixels": 298249},
                        "score": None,
                    },
                ]
                # [
                #     {
                #         "score": 0.004,
                #         "label": "LABEL_215",
                #         "mask": {"hash": "34eecd16bb", "shape": (480, 640), "white_pixels": 0},
                #     },
                #     {
                #         "score": 0.004,
                #         "label": "LABEL_215",
                #         "mask": {"hash": "34eecd16bb", "shape": (480, 640), "white_pixels": 0},
                #     },
                # ],
                # [
                #     {
                #         "score": 0.004,
                #         "label": "LABEL_215",
                #         "mask": {"hash": "34eecd16bb", "shape": (480, 640), "white_pixels": 0},
                #     },
                #     {
                #         "score": 0.004,
                #         "label": "LABEL_215",
                #         "mask": {"hash": "34eecd16bb", "shape": (480, 640), "white_pixels": 0},
                #     },
                # ],
            ],
        )

    @require_torch
    def test_small_model_pt_semantic(self):
        model_id = "hf-internal-testing/tiny-random-beit-pipeline"
        image_segmenter = pipeline(model=model_id)
        outputs = image_segmenter("http://images.cocodataset.org/val2017/000000039769.jpg")
        for o in outputs:
            # shortening by hashing
            o["mask"] = mask_to_test_readable(o["mask"])

        self.assertEqual(
            nested_simplify(outputs, decimals=4),
            [
                {
                    "score": None,
                    "label": "LABEL_0",
                    "mask": {"hash": "42d0907228", "shape": (480, 640), "white_pixels": 10714},
                },
                {
                    "score": None,
                    "label": "LABEL_1",
                    "mask": {"hash": "46b8cc3976", "shape": (480, 640), "white_pixels": 296486},
                },
            ],
        )

    @require_torch
    @slow
    def test_integration_torch_image_segmentation(self):
        model_id = "facebook/detr-resnet-50-panoptic"
        image_segmenter = pipeline("image-segmentation", model=model_id)

        outputs = image_segmenter(
            "http://images.cocodataset.org/val2017/000000039769.jpg",
            task="panoptic",
            threshold=0,
            overlap_mask_area_threshold=0.0,
        )

        # Shortening by hashing
        for o in outputs:
            o["mask"] = mask_to_test_readable(o["mask"])

        self.assertEqual(
            nested_simplify(outputs, decimals=4),
            [
                {
                    "score": 0.9094,
                    "label": "blanket",
                    "mask": {"hash": "dcff19a97a", "shape": (480, 640), "white_pixels": 16617},
                },
                {
                    "score": 0.9941,
                    "label": "cat",
                    "mask": {"hash": "9c0af87bd0", "shape": (480, 640), "white_pixels": 59185},
                },
                {
                    "score": 0.9987,
                    "label": "remote",
                    "mask": {"hash": "c7870600d6", "shape": (480, 640), "white_pixels": 4182},
                },
                {
                    "score": 0.9995,
                    "label": "remote",
                    "mask": {"hash": "ef899a25fd", "shape": (480, 640), "white_pixels": 2275},
                },
                {
                    "score": 0.9722,
                    "label": "couch",
                    "mask": {"hash": "37b8446ac5", "shape": (480, 640), "white_pixels": 172380},
                },
                {
                    "score": 0.9994,
                    "label": "cat",
                    "mask": {"hash": "6a09d3655e", "shape": (480, 640), "white_pixels": 52561},
                },
            ],
        )

        outputs = image_segmenter(
            [
                "http://images.cocodataset.org/val2017/000000039769.jpg",
                "http://images.cocodataset.org/val2017/000000039769.jpg",
            ],
            task="panoptic",
            threshold=0.0,
            overlap_mask_area_threshold=0.0,
        )

        # Shortening by hashing
        for output in outputs:
            for o in output:
                o["mask"] = mask_to_test_readable(o["mask"])

        self.assertEqual(
            nested_simplify(outputs, decimals=4),
            [
                [
                    {
                        "score": 0.9094,
                        "label": "blanket",
                        "mask": {"hash": "dcff19a97a", "shape": (480, 640), "white_pixels": 16617},
                    },
                    {
                        "score": 0.9941,
                        "label": "cat",
                        "mask": {"hash": "9c0af87bd0", "shape": (480, 640), "white_pixels": 59185},
                    },
                    {
                        "score": 0.9987,
                        "label": "remote",
                        "mask": {"hash": "c7870600d6", "shape": (480, 640), "white_pixels": 4182},
                    },
                    {
                        "score": 0.9995,
                        "label": "remote",
                        "mask": {"hash": "ef899a25fd", "shape": (480, 640), "white_pixels": 2275},
                    },
                    {
                        "score": 0.9722,
                        "label": "couch",
                        "mask": {"hash": "37b8446ac5", "shape": (480, 640), "white_pixels": 172380},
                    },
                    {
                        "score": 0.9994,
                        "label": "cat",
                        "mask": {"hash": "6a09d3655e", "shape": (480, 640), "white_pixels": 52561},
                    },
                ],
                [
                    {
                        "score": 0.9094,
                        "label": "blanket",
                        "mask": {"hash": "dcff19a97a", "shape": (480, 640), "white_pixels": 16617},
                    },
                    {
                        "score": 0.9941,
                        "label": "cat",
                        "mask": {"hash": "9c0af87bd0", "shape": (480, 640), "white_pixels": 59185},
                    },
                    {
                        "score": 0.9987,
                        "label": "remote",
                        "mask": {"hash": "c7870600d6", "shape": (480, 640), "white_pixels": 4182},
                    },
                    {
                        "score": 0.9995,
                        "label": "remote",
                        "mask": {"hash": "ef899a25fd", "shape": (480, 640), "white_pixels": 2275},
                    },
                    {
                        "score": 0.9722,
                        "label": "couch",
                        "mask": {"hash": "37b8446ac5", "shape": (480, 640), "white_pixels": 172380},
                    },
                    {
                        "score": 0.9994,
                        "label": "cat",
                        "mask": {"hash": "6a09d3655e", "shape": (480, 640), "white_pixels": 52561},
                    },
                ],
            ],
        )

    @require_torch
    @slow
    def test_threshold(self):
        model_id = "facebook/detr-resnet-50-panoptic"
        image_segmenter = pipeline("image-segmentation", model=model_id)

        outputs = image_segmenter(
            "http://images.cocodataset.org/val2017/000000039769.jpg", task="panoptic", threshold=0.999
        )
        # Shortening by hashing
        for o in outputs:
            o["mask"] = mask_to_test_readable(o["mask"])

        self.assertEqual(
            nested_simplify(outputs, decimals=4),
            [
                {
                    "score": 0.9995,
                    "label": "remote",
                    "mask": {"hash": "d02404f578", "shape": (480, 640), "white_pixels": 2789},
                },
                {
                    "score": 0.9994,
                    "label": "cat",
                    "mask": {"hash": "eaa115b40c", "shape": (480, 640), "white_pixels": 304411},
                },
            ],
        )

        outputs = image_segmenter(
            "http://images.cocodataset.org/val2017/000000039769.jpg", task="panoptic", threshold=0.5
        )

        for o in outputs:
            o["mask"] = mask_to_test_readable(o["mask"])

        self.assertEqual(
            nested_simplify(outputs, decimals=4),
            [
                {
                    "score": 0.9941,
                    "label": "cat",
                    "mask": {"hash": "9c0af87bd0", "shape": (480, 640), "white_pixels": 59185},
                },
                {
                    "score": 0.9987,
                    "label": "remote",
                    "mask": {"hash": "c7870600d6", "shape": (480, 640), "white_pixels": 4182},
                },
                {
                    "score": 0.9995,
                    "label": "remote",
                    "mask": {"hash": "ef899a25fd", "shape": (480, 640), "white_pixels": 2275},
                },
                {
                    "score": 0.9722,
                    "label": "couch",
                    "mask": {"hash": "37b8446ac5", "shape": (480, 640), "white_pixels": 172380},
                },
                {
                    "score": 0.9994,
                    "label": "cat",
                    "mask": {"hash": "6a09d3655e", "shape": (480, 640), "white_pixels": 52561},
                },
            ],
        )

    @require_torch
    @slow
    def test_maskformer(self):
        threshold = 0.8
        model_id = "facebook/maskformer-swin-base-ade"

        model = AutoModelForInstanceSegmentation.from_pretrained(model_id)
        feature_extractor = AutoFeatureExtractor.from_pretrained(model_id)

        image_segmenter = pipeline("image-segmentation", model=model, feature_extractor=feature_extractor)

        image = load_dataset("hf-internal-testing/fixtures_ade20k", split="test")
        file = image[0]["file"]
        outputs = image_segmenter(file, task="panoptic", threshold=threshold)

        # Shortening by hashing
        for o in outputs:
            o["mask"] = mask_to_test_readable(o["mask"])

        self.assertEqual(
            nested_simplify(outputs, decimals=4),
            [
                {
                    "score": 0.9974,
                    "label": "wall",
                    "mask": {"hash": "a547b7c062", "shape": (512, 683), "white_pixels": 14252},
                },
                {
                    "score": 0.949,
                    "label": "house",
                    "mask": {"hash": "0da9b7b38f", "shape": (512, 683), "white_pixels": 132177},
                },
                {
                    "score": 0.9995,
                    "label": "grass",
                    "mask": {"hash": "1d07ea0a26", "shape": (512, 683), "white_pixels": 53444},
                },
                {
                    "score": 0.9976,
                    "label": "tree",
                    "mask": {"hash": "6cdc97c7da", "shape": (512, 683), "white_pixels": 7944},
                },
                {
                    "score": 0.8239,
                    "label": "plant",
                    "mask": {"hash": "1ab4ce378f", "shape": (512, 683), "white_pixels": 4136},
                },
                {
                    "score": 0.9942,
                    "label": "road, route",
                    "mask": {"hash": "39c5d17be5", "shape": (512, 683), "white_pixels": 1941},
                },
                {
                    "score": 1.0,
                    "label": "sky",
                    "mask": {"hash": "a3756324a6", "shape": (512, 683), "white_pixels": 135802},
                },
            ],
        )

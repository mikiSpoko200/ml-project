import argparse
import json
import itertools
import pickle
import os

import PIL
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from PIL.Image import Image
from sklearn.neighbors import KNeighborsClassifier

import loader
import match
import palette
import utils
import config

from config import default_config

from typing import Iterator

# Emulate conditional compilation
if config.PROFILE:
    def tqdm(*args, **_):
        return args[0]
else:
    from tqdm import tqdm


def feature_batches(features_iterator: loader.BatchLoader) -> Iterator[list[Image]]:
    samples = list()
    samples.clear()
    for features in tqdm(itertools.zip_longest(*features_iterator), desc="class batches"):
        samples.clear()
        samples.extend(itertools.chain.from_iterable(
            (image for image in image_batch) for image_batch in features if image_batch is not None)
        )
        yield samples


Class = str
TARGET = utils.ClassificationTarget.ARTIST


def predict1(
        image: Image,
        global_palette: np.ndarray,
        class_histograms: dict[Class, np.ndarray],
        neighbours: KNeighborsClassifier,
) -> Class:
    (histogram, _) = match.match1(image, global_palette, neighbours)
    difference = dict()
    for cls, cls_histogram in class_histograms.items():
        difference[cls] = abs(cls_histogram - histogram).sum()

    return min(difference, key=difference.get)


def method1(batch_loader: loader.BatchLoader, loader_params: list, pickling: bool = True, loading: bool = False):
    # CREATING GLOBAL PALETTE
    palettes = list()
    palettes_dir = None
    histograms_dir = None
    if pickling:
        palettes_dir = os.path.join(os.path.dirname(__file__), "palettes")
        histograms_dir = os.path.join(os.path.dirname(__file__), "histograms")
        if not os.path.exists(palettes_dir):
            os.makedirs(palettes_dir, exist_ok=True)
        if not os.path.exists(palettes_dir):
            os.makedirs(histograms_dir, exist_ok=True)

    if not loading:
        for idx, image_batch in enumerate(tqdm(feature_batches(batch_loader), desc=" features")):
            curr_palette = palette.generate_palette(image_batch, default_config.global_palette, verbose=True)
            palettes.append(curr_palette)
            print(f"Generated palette nr {idx}")

            if pickling:
                assert palettes_dir is not None
                pickle.dump(curr_palette, open(os.path.join(palettes_dir, f"palette{idx}"), "wb"))

        global_palette = palette.merge_palettes(palettes, default_config.global_palette)
        if pickling:
            pickle.dump(global_palette, open(os.path.join(os.path.dirname(__file__), "global_palette"), "wb"))
        fig = palette.plot_palette(global_palette, default_config.global_palette).savefig(
            os.path.join(os.path.dirname(__file__), f"global_palette.png"))
        plt.close()
    else:
        global_palette = pickle.load(open(os.path.join(os.path.dirname(__file__), "global_palette"), "rb"))

    # CALCULATING AVERAGE CLASS HISTOGRAMS
    neighbours = KNeighborsClassifier(n_neighbors=1).fit(global_palette, np.arange(global_palette.shape[0]))

    if not loading:
        class_histograms = dict()
        for feature_batch_iterator in loader.BatchLoader(*loader_params):
            avg_histogram = np.zeros((default_config.global_palette.size,))
            total_patch_count = 0

            # Generate averaged class histogram
            for image_batch in tqdm(feature_batch_iterator, desc=" feature"):
                for image in tqdm(image_batch, desc=" batch"):
                    # TODO: image -> np.ndarray should have it's own function,
                    #  and in general structure of this code duplicates -- fix it.
                    (histogram, patches_count) = match.match1(
                        # TODO: image dimensions are hard coded here
                        image,
                        global_palette,
                        neighbours
                    )
                    total_patch_count += patches_count
                    avg_histogram += histogram
            avg_histogram /= total_patch_count
            class_histograms[feature_batch_iterator.cls] = avg_histogram
            if pickling:
                assert histograms_dir is not None
                pickle.dump(avg_histogram, open(os.path.join(histograms_dir, f"{feature_batch_iterator.cls}"), "wb"))

        if pickling:
            pickle.dump(class_histograms, open(os.path.join(os.path.dirname(__file__), "class_histograms"), "wb"))
    else:
        class_histograms = pickle.load(open(os.path.join(os.path.dirname(__file__), "class_histograms"), "rb"))

    # VALIDATION
    batch_loader._cls_encoding(TARGET)
    val_entries = pd.read_csv(
        os.path.join(default_config.dataset_labels_path, f"{batch_loader.target.name.lower()}_val.csv"),
        names=["path", "encoded_cls"])

    correct, all_entries = 0, 0
    class_encoding = batch_loader._cls_encoding(TARGET)
    for _, entry in val_entries.iterrows():
        try:
            with PIL.Image.open(os.path.join(default_config.dataset_path, entry['path'])) as sample:
                prediction = predict1(sample, global_palette, class_histograms, neighbours)
                target = class_encoding[entry["encoded_cls"]]
                print(f"target: {target}, prediction: {prediction}")
                correct += (prediction == target)
                all_entries += 1
        except FileNotFoundError:
            continue
    print(correct / all_entries)


def predict2(
        image: Image,
        local_palettes: dict[Class, np.ndarray],
        neighbours: dict[Class, KNeighborsClassifier],
):
    matches = dict()

    for cls, cls_palette in local_palettes.items():
        cls_match = match.match2(image, cls_palette, neighbours[cls])
        matches[cls] = cls_match

    # TODO: how to pick closest class? minimum sum of distances for now
    sums = {cls: np.sum(matches[cls][0]) for cls in matches.keys()}
    return min(sums, key=sums.get)


def method2(batch_loader: loader.BatchLoader, pickling: bool = True, loading: bool = False):
    # GENERATING LOCAL (CLASS) PALETTES
    local_palettes = dict()
    neighbours = dict()
    if pickling:
        palettes_dir = os.path.join(os.path.dirname(__file__), "loc_palettes")
        if not os.path.exists(palettes_dir):
            os.makedirs(palettes_dir, exist_ok=True)

    if loading:
        local_palettes = pickle.load(open(os.path.join(os.path.dirname(__file__), "local_palettes"), "rb"))
    else:
        palette_images_dir = os.path.join(os.path.dirname(__file__), "loc_palette_images")
        if not os.path.exists(palette_images_dir):
            os.makedirs(palette_images_dir, exist_ok=True)

    for feature_batch_iterator in batch_loader:
        cls = feature_batch_iterator.cls
        # if cls != "Vincent_van_Gogh" and cls != "Pablo_Picasso":
        #     continue

        print(f'\n{cls}')
        if not loading:
            palettes = list()
            for idx, image_batch in enumerate(tqdm(feature_batch_iterator, desc=" feature")):
                palettes.append(palette.generate_palette(image_batch, default_config.local_palette))
                print(f"Generated palette nr {idx}")

            local_palette = palette.merge_palettes(palettes, default_config.local_palette)
            local_palettes[cls] = local_palette

            fig = palette.plot_palette(local_palettes[cls], default_config.local_palette).savefig(
                os.path.join(palette_images_dir, f"{cls}.png"))
            plt.close()
        neighbours[cls] = KNeighborsClassifier(n_neighbors=1).fit(local_palettes[cls],
                                                                  np.arange(local_palettes[cls].shape[0]))

        if pickling:
            pickle.dump(local_palettes[cls], open(os.path.join(palettes_dir, f"{cls}"), "wb"))

    if pickling:
        pickle.dump(local_palettes, open(os.path.join(os.path.dirname(__file__), "local_palettes"), "wb"))

    # VALIDATION
    val_entries = pd.read_csv(
        os.path.join(default_config.dataset_labels_path, f"{batch_loader.target.name.lower()}_val.csv"),
        names=["path", "encoded_cls"])

    # only Van Gogh and Picasso
    # val_entries = val_entries[(val_entries["encoded_cls"] == 15) | (val_entries["encoded_cls"] == 22)]

    correct, all_entries = 0, 0
    class_encoding = batch_loader._cls_encoding(TARGET)
    for _, entry in val_entries.sample(frac=0.1).iterrows():
        try:
            with PIL.Image.open(os.path.join("./cut_wikiart/", entry['path'])) as sample:
                prediction = predict2(sample, local_palettes, neighbours)
                target = class_encoding[entry["encoded_cls"]]
                # print(f"target: {target}, prediction: {prediction}")
                correct += (prediction == target)
                all_entries += 1
                print(f'\r{correct / all_entries}', end="")
        except FileNotFoundError:
            continue
    # print(correct / all_entries)


def main():
    parser = argparse.ArgumentParser(description="Process some integers.")
    parser.add_argument("--config", type=str, default="./config.json", help="Path to the configuration file")
    parser.add_argument("--dataset-path", type=str, help="Override path to the dataset specified in --config")
    parser.add_argument("--dataset-labels-path", type=str,
                        help="Override path to the dataset labels specified in --config")
    parser.add_argument("--batch-size", nargs=2, type=str,
                        help="Override batch size for the loader specified in --config. Format: <value> <unit> (e.g. 256 MiB)")

    args = parser.parse_args()

    with open(args.config, "r") as config_file:
        config_json = json.load(config_file)

    # optionally override default config
    config.default_config = config.Config.from_json(config_json)

    subrandom_index = None
    if os.path.exists("./subrandom-index.json"):
        with open("./subrandom-index.json") as index:
            deserialized_raw = json.load(index)
            subrandom_index = dict()
            for target in deserialized_raw:
                subrandom_index[utils.ClassificationTarget[target]] = deserialized_raw[target]

    batch_loader = loader.BatchLoader(TARGET, index=subrandom_index)

    # method1(batch_loader, loader_params, config)

    method2(batch_loader, loading=True)


if __name__ == "__main__":
    try:
        main()
    finally:
        pass

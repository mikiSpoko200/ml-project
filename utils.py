import enum

import matplotlib as plt
import numpy as np

from PIL import Image
from sklearn.feature_extraction.image import extract_patches_2d
from sklearn.neighbors import KNeighborsClassifier

from config import Config, GlobalPaletteConfig


class ClassificationTarget(enum.Enum):
    """Enumeration representing the target features for classification."""
    ARTIST = enum.auto()
    GENRE = enum.auto()
    STYLE = enum.auto()


# TODO: move this to "*-palette" configurations
PATCH_SIZE: int = 16  # patches PATCH_SIZE x PATCH_SIZE


BASIC_COLORS = np.array(
    [
        [255, 0, 0],
        [255, 128, 0],
        [255, 255, 0],
        [128, 255, 0],
        [0, 255, 0],
        [0, 255, 128],
        [0, 255, 255],
        [0, 128, 255],
        [0, 0, 255],
        [128, 0, 255],
        [255, 0, 255],
        [255, 0, 128],
    ]
)


def read_image(path):
    image = Image.open(path)
    return image


def get_patches(image: np.ndarray, config: GlobalPaletteConfig):
    height, width = image.shape[0], image.shape[1]
    assert height >= PATCH_SIZE and width >= PATCH_SIZE

    if config.random:
        patches_count = int(
            config.coverage * (height - PATCH_SIZE + 1) * (width - PATCH_SIZE + 1)
        )
        patches = extract_patches_2d(
            image, (PATCH_SIZE, PATCH_SIZE), max_patches=patches_count
        ).reshape((-1, PATCH_SIZE * PATCH_SIZE * 3))
    else:
        # calculate strides
        # TODO: move strides to config
        stride_y, stride_x = 1, 1
        curr_coverage = 1.0
        iter = 0
        while curr_coverage > config.coverage:
            curr_coverage = 1.0 / (stride_x * stride_y)
            if iter % 2 == 0:
                stride_y += 1
            else:
                stride_x += 1

        # sliding window
        patches = []
        for upper_left_y in range(0, height - PATCH_SIZE + 1, stride_y):
            for upper_left_x in range(0, width - PATCH_SIZE + 1, stride_x):
                patch = image[
                    upper_left_y: upper_left_y + PATCH_SIZE,
                    upper_left_x: upper_left_x + PATCH_SIZE,
                    :,
                ].reshape((PATCH_SIZE * PATCH_SIZE * 3))
                patches.append(patch)
    return np.array(patches)


def k_closest(patches: np.ndarray, palette: np.ndarray, k: int, neigh: KNeighborsClassifier | None = None):
    # TODO: run with n_jobs? - to test
    if neigh is not None:
        neigh = KNeighborsClassifier(n_neighbors=k)
        neigh.fit(palette, np.arange(palette.shape[0]))
    closest = neigh.kneighbors(patches)

    return closest


def histogram(neighbors: np.ndarray, patches_num: int) -> np.ndarray:
    neighbors = neighbors.flatten()
    hist = np.zeros((neighbors.shape[0],))
    hist[neighbors] += 1
    _, histogram = np.unique(neighbors, return_counts=True)
    histogram = histogram.astype("float64")
    histogram /= patches_num

    return histogram

def plot_image(x, size):
    plt.figure(figsize=(1.5, 1.5))
    plt.imshow(x.reshape(size, size, 3))
    plt.show()
    plt.close()

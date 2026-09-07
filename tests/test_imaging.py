import numpy as np
import pytest

from object_select.imaging import make_trimap, mask_image


def test_trimap_has_foreground_background_and_unknown_boundary():
    mask = np.zeros((40, 40))
    mask[10:30, 10:30] = 1
    trimap = make_trimap(mask, 3)
    assert trimap[20, 20] == 255
    assert trimap[0, 0] == 0
    assert trimap[10, 15] == trimap[9, 15] == 128
    with pytest.raises(ValueError, match="Reduce"):
        make_trimap(mask, 30)


def test_soft_export_preserves_fractional_values():
    assert np.asarray(mask_image(np.array([[0, 0.5, 1]]))).tolist() == [[0, 128, 255]]

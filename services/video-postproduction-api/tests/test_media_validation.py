from __future__ import annotations

import pytest

from video_postproduction.media.validation import (
    UploadValidationError,
    validate_media_type,
    validate_upload_filename,
)


def test_upload_validator_rejects_unknown_type():
    with pytest.raises(UploadValidationError, match="not supported"):
        validate_media_type("application/x-executable", "video.mp4")


def test_upload_validator_rejects_suffix_mismatch():
    with pytest.raises(UploadValidationError, match="does not match"):
        validate_media_type("video/mp4", "video.mov")


def test_upload_filename_rejects_path_segments():
    with pytest.raises(UploadValidationError, match="filename"):
        validate_upload_filename("../video.mp4")


def test_upload_validator_returns_safe_normalized_values():
    assert validate_upload_filename("video.MP4") == "video.MP4"
    assert validate_media_type(" Video/MP4 ", "video.mp4") == "video/mp4"

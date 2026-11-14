# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from importlib.metadata import version

import siliconsmith._version as version_module


def test_installed_version_matches_source() -> None:
    assert version("siliconsmith") == version_module.__version__

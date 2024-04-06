import json
import logging
import os

import pytest
from aioresponses import aioresponses
from homeassistant.core import HomeAssistant

from custom_components.powercalc.helpers import get_library_json_path
from custom_components.powercalc.power_profile.error import LibraryLoadingError, ProfileDownloadError
from custom_components.powercalc.power_profile.loader.remote import ENDPOINT_DOWNLOAD, ENDPOINT_LIBRARY, RemoteLoader
from custom_components.powercalc.power_profile.power_profile import DeviceType
from tests.common import get_test_profile_dir

pytestmark = pytest.mark.skip_remote_loader_mocking

@pytest.fixture
def mock_aioresponse() -> aioresponses:
    with aioresponses() as m:
        yield m


@pytest.fixture
def mock_library_json_response(mock_aioresponse: aioresponses) -> None:
    local_library_path = get_library_json_path()
    with open(local_library_path) as f:
        library_json = json.load(f)

    mock_aioresponse.get(
        ENDPOINT_LIBRARY,
        status=200,
        payload=library_json,
    )


@pytest.fixture
async def remote_loader(hass: HomeAssistant, mock_library_json_response: None) -> RemoteLoader:
    loader = RemoteLoader(hass)
    await loader.initialize()
    return loader


async def test_download(mock_aioresponse: aioresponses, remote_loader: RemoteLoader) -> None:
    """Mock the API response for the download of a profile."""
    remote_files = [
        {"path": "color_temp.csv.gz", "url": "https://raw.githubusercontent.com/bramstroker/homeassistant-powercalc/master/custom_components/powercalc/data/signify/LCA001/color_temp.csv.gz"},
        {"path": "hs.csv.gz", "url": "https://raw.githubusercontent.com/bramstroker/homeassistant-powercalc/master/custom_components/powercalc/data/signify/LCA001/hs.csv.gz"},
        {"path": "model.json", "url": "https://raw.githubusercontent.com/bramstroker/homeassistant-powercalc/master/custom_components/powercalc/data/signify/LCA001/model.json"},
    ]

    mock_aioresponse.get(
        f"{ENDPOINT_DOWNLOAD}/signify/LCA001",
        status=200,
        payload=remote_files,
    )

    for remote_file in remote_files:
        with open(get_test_profile_dir("signify-LCA001") + f"/{remote_file['path']}", "rb") as f:
            mock_aioresponse.get(
                remote_file["url"],
                status=200,
                body=f.read(),
            )

    storage_dir = get_test_profile_dir("download")
    await remote_loader.download_profile("signify", "LCA001", storage_dir)

    for remote_file in remote_files:
        assert os.path.exists(os.path.join(storage_dir, remote_file["path"]))


async def test_get_manufacturer_listing(remote_loader: RemoteLoader) -> None:
    manufacturers = await remote_loader.get_manufacturer_listing(DeviceType.LIGHT)
    assert "signify" in manufacturers
    assert len(manufacturers) > 40


async def test_get_model_listing(remote_loader: RemoteLoader) -> None:
    models = await remote_loader.get_model_listing("signify", DeviceType.LIGHT)
    assert "LCT010" in models
    assert len(models) > 40


async def test_fallback_to_local_library(hass: HomeAssistant, mock_aioresponse: aioresponses, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.ERROR)
    mock_aioresponse.get(
        ENDPOINT_LIBRARY,
        status=404,
        repeat=True,
    )

    loader = RemoteLoader(hass)
    await loader.initialize()

    assert "signify" in loader.manufacturer_models
    assert len(caplog.records) == 1


async def test_load_model_ignores_local_directory(remote_loader: RemoteLoader) -> None:
    assert not await remote_loader.load_model("signify", "LCA001", get_test_profile_dir("signify-LCA001"))


async def test_load_model_raises_library_exception_on_non_existing_model(remote_loader: RemoteLoader) -> None:
    with pytest.raises(LibraryLoadingError):
        await remote_loader.load_model("signify", "NON_EXISTING_MODEL", None)


async def test_download_profile_exception_unexpected_status_code(mock_aioresponse: aioresponses, remote_loader: RemoteLoader) -> None:
    mock_aioresponse.get(
        f"{ENDPOINT_DOWNLOAD}/signify/LCA001",
        status=500,
    )

    with pytest.raises(ProfileDownloadError):
        await remote_loader.download_profile("signify", "LCA001", get_test_profile_dir("download"))


async def _create_loader(hass: HomeAssistant) -> RemoteLoader:
    loader = RemoteLoader(hass)
    await loader.initialize()
    return loader

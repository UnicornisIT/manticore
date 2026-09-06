import tempfile
import unittest
from pathlib import Path
from unittest import mock

from desktop import windows_client


class WindowsExternalLinksTests(unittest.TestCase):
    def test_desktop_keeps_external_links_in_system_browser(self):
        webview = mock.MagicMock()
        # A changed dependency default must not put developer sites in WebView2.
        webview.settings = {"OPEN_EXTERNAL_LINKS_IN_BROWSER": False}
        with tempfile.TemporaryDirectory(prefix="manticore_links_") as directory:
            root = Path(directory)
            with (
                mock.patch.dict("sys.modules", {"webview": webview}),
                mock.patch("desktop.windows_client.application_data_directory", return_value=root / "data"),
                mock.patch("desktop.windows_client.bundle_root", return_value=root),
            ):
                windows_client.open_desktop_window("https://manticore.example.test")

        self.assertIs(webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"], True)
        self.assertIs(webview.settings["ALLOW_DOWNLOADS"], True)

    def test_desktop_title_and_api_use_the_bundled_version(self):
        webview = mock.MagicMock()
        webview.settings = {}
        with tempfile.TemporaryDirectory(prefix="manticore_version_") as directory:
            root = Path(directory)
            (root / "VERSION").write_text("9.8.7-rc.1\n", encoding="utf-8")
            with (
                mock.patch.dict("sys.modules", {"webview": webview}),
                mock.patch("desktop.windows_client.application_data_directory", return_value=root / "data"),
                mock.patch("desktop.windows_client.bundle_root", return_value=root),
            ):
                windows_client.open_desktop_window("https://manticore.example.test")
                api = webview.create_window.call_args.kwargs["js_api"]
                self.assertEqual(api.get_current_version(), "9.8.7-rc.1")

        self.assertEqual(webview.create_window.call_args.args[0], "Manticore 9.8.7-rc.1")


if __name__ == "__main__":
    unittest.main()

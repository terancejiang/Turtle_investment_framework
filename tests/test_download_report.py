"""Tests for scripts/download_report.py"""

import os
import sys
import tempfile
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from download_report import (
    EXIT_BAD_ARGUMENTS,
    EXIT_NETWORK_FAILURE,
    EXIT_PDF_VALIDATION_FAILURE,
    EXIT_SUCCESS,
    build_filename,
    download_annual_report,
    get_headers,
    main,
    print_result,
    validate_url,
)


# --- TestValidateUrl ---

class TestValidateUrl:
    def test_valid_xueqiu_url(self):
        ok, msg = validate_url("https://stockn.xueqiu.com/some/path/report.pdf")
        assert ok is True
        assert msg == ""

    def test_valid_10jqka_url(self):
        ok, msg = validate_url("https://notice.10jqka.com.cn/api/report.pdf")
        assert ok is True
        assert msg == ""

    def test_valid_10jqka_subdomain(self):
        ok, msg = validate_url("https://data.10jqka.com.cn/path/file.pdf")
        assert ok is True
        assert msg == ""

    def test_invalid_domain(self):
        ok, msg = validate_url("https://example.com/report.pdf")
        assert ok is False
        assert "Invalid URL" in msg

    def test_non_pdf_url(self):
        ok, msg = validate_url("https://stockn.xueqiu.com/report.html")
        assert ok is False
        assert "Invalid URL" in msg

    def test_http_also_valid(self):
        ok, msg = validate_url("http://stockn.xueqiu.com/report.pdf")
        assert ok is True

    def test_empty_url(self):
        ok, msg = validate_url("")
        assert ok is False

    def test_case_insensitive(self):
        ok, msg = validate_url("HTTPS://STOCKN.XUEQIU.COM/REPORT.PDF")
        assert ok is True


# --- TestBuildFilename ---

class TestBuildFilename:
    def test_chinese_annual(self):
        assert build_filename("SH600887", "年报", "2024") == "600887_2024_年报.pdf"

    def test_chinese_interim(self):
        assert build_filename("SZ300750", "中报", "2024") == "300750_2024_中报.pdf"

    def test_english_annual(self):
        assert build_filename("SH600887", "annual", "2024") == "600887_2024_年报.pdf"

    def test_english_interim(self):
        assert build_filename("SH600887", "interim", "2024") == "600887_2024_中报.pdf"

    def test_english_q1(self):
        assert build_filename("SH600887", "q1", "2024") == "600887_2024_一季报.pdf"

    def test_english_q3(self):
        assert build_filename("SH600887", "Q3", "2024") == "600887_2024_三季报.pdf"

    def test_hk_stock_no_prefix(self):
        assert build_filename("00700", "年报", "2024") == "00700_2024_年报.pdf"

    def test_strips_sh_prefix(self):
        result = build_filename("SH600887", "年报", "2024")
        assert result.startswith("600887_")

    def test_strips_sz_prefix(self):
        result = build_filename("SZ300750", "年报", "2024")
        assert result.startswith("300750_")

    def test_lowercase_prefix_stripped(self):
        result = build_filename("sh600887", "年报", "2024")
        assert result.startswith("600887_")


# --- TestGetHeaders ---

class TestGetHeaders:
    def test_xueqiu_referer(self):
        headers = get_headers("https://stockn.xueqiu.com/report.pdf")
        assert headers["Referer"] == "https://xueqiu.com/"

    def test_10jqka_referer(self):
        headers = get_headers("https://notice.10jqka.com.cn/report.pdf")
        assert headers["Referer"] == "https://10jqka.com.cn/"

    def test_base_headers_present(self):
        headers = get_headers("https://stockn.xueqiu.com/report.pdf")
        assert "User-Agent" in headers
        assert "Accept" in headers
        assert "Accept-Language" in headers


# --- TestDownloadAnnualReport ---

class TestDownloadAnnualReport:
    def _make_pdf_response(self, content=None, content_type="application/pdf"):
        """Create a mock response that behaves like a streaming PDF download."""
        if content is None:
            content = b"%PDF-1.4 fake pdf content here" + b"\x00" * 1024
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": content_type}
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_content = MagicMock(return_value=[content])
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    @patch("download_report.requests.get")
    def test_successful_download(self, mock_get):
        mock_get.return_value = self._make_pdf_response()
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = os.path.join(tmpdir, "test.pdf")
            success, msg, size = download_annual_report(
                "https://stockn.xueqiu.com/test.pdf", save_path, max_retries=1
            )
            assert success is True
            assert "successful" in msg.lower()
            assert size > 0
            assert os.path.exists(save_path)

    @patch("download_report.requests.get")
    def test_pdf_magic_bytes_failure(self, mock_get):
        mock_get.return_value = self._make_pdf_response(content=b"<html>not a pdf</html>")
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = os.path.join(tmpdir, "test.pdf")
            success, msg, size = download_annual_report(
                "https://stockn.xueqiu.com/test.pdf", save_path, max_retries=1
            )
            assert success is False
            assert "magic bytes" in msg.lower()
            assert size == 0

    @patch("download_report.requests.get")
    @patch("download_report.time.sleep")
    def test_retry_on_network_error(self, mock_sleep, mock_get):
        import requests as req
        mock_get.side_effect = [
            req.exceptions.ConnectionError("connection refused"),
            self._make_pdf_response(),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = os.path.join(tmpdir, "test.pdf")
            success, msg, size = download_annual_report(
                "https://stockn.xueqiu.com/test.pdf", save_path, max_retries=2
            )
            assert success is True
            assert mock_get.call_count == 2
            mock_sleep.assert_called_once_with(3)  # BACKOFF_BASE * 1

    @patch("download_report.requests.get")
    @patch("download_report.time.sleep")
    def test_all_retries_exhausted(self, mock_sleep, mock_get):
        import requests as req
        mock_get.side_effect = req.exceptions.ConnectionError("connection refused")
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = os.path.join(tmpdir, "test.pdf")
            success, msg, size = download_annual_report(
                "https://stockn.xueqiu.com/test.pdf", save_path, max_retries=2
            )
            assert success is False
            assert "failed after 2 attempts" in msg.lower()
            assert mock_get.call_count == 2

    @patch("download_report.requests.get")
    def test_tmp_file_cleaned_on_magic_failure(self, mock_get):
        mock_get.return_value = self._make_pdf_response(content=b"NOT_PDF_CONTENT")
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = os.path.join(tmpdir, "test.pdf")
            download_annual_report(
                "https://stockn.xueqiu.com/test.pdf", save_path, max_retries=1
            )
            assert not os.path.exists(save_path + ".tmp")
            assert not os.path.exists(save_path)

    @patch("download_report.requests.get")
    def test_content_type_warning(self, mock_get, capsys):
        mock_get.return_value = self._make_pdf_response(content_type="text/html")
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = os.path.join(tmpdir, "test.pdf")
            download_annual_report(
                "https://stockn.xueqiu.com/test.pdf", save_path, max_retries=1
            )
            captured = capsys.readouterr()
            assert "Content-Type" in captured.err


# --- TestPrintResult ---

class TestPrintResult:
    def test_success_format(self, capsys):
        print_result(
            success=True, filepath="/tmp/test.pdf", filesize=12345,
            url="https://example.com/test.pdf", stock_code="SH600887",
            report_type="年报", year="2024", message="OK"
        )
        out = capsys.readouterr().out
        assert "---RESULT---" in out
        assert "status: SUCCESS" in out
        assert "filepath: /tmp/test.pdf" in out
        assert "filesize: 12345" in out
        assert "---END---" in out

    def test_failure_format(self, capsys):
        print_result(success=False, message="Download failed")
        out = capsys.readouterr().out
        assert "status: FAILED" in out
        assert "message: Download failed" in out

    def test_all_fields_present(self, capsys):
        print_result(
            success=True, filepath="p", filesize=1, url="u",
            stock_code="s", report_type="r", year="y", message="m"
        )
        out = capsys.readouterr().out
        for field in ["status", "filepath", "filesize", "url", "stock_code",
                       "report_type", "year", "message"]:
            assert f"{field}:" in out


# --- TestMain ---

class TestMain:
    @patch("download_report.download_annual_report")
    @patch("download_report.validate_url", return_value=(True, ""))
    def test_success_flow(self, mock_validate, mock_download):
        mock_download.return_value = (True, "OK", 50000)
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(SystemExit) as exc_info:
                main([
                    "--url", "https://stockn.xueqiu.com/test.pdf",
                    "--stock-code", "SH600887",
                    "--report-type", "年报",
                    "--year", "2024",
                    "--save-dir", tmpdir,
                ])
            assert exc_info.value.code == EXIT_SUCCESS

    def test_bad_url_exit_code(self):
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--url", "https://example.com/bad.pdf",
                "--stock-code", "SH600887",
                "--report-type", "年报",
                "--year", "2024",
            ])
        assert exc_info.value.code == EXIT_BAD_ARGUMENTS

    def test_missing_url_requires_xueqiu_args(self):
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--stock-code", "SH600887",
                "--report-type", "年报",
                "--year", "2024",
            ])
        assert exc_info.value.code == EXIT_BAD_ARGUMENTS

    @patch("download_report.download_annual_report")
    @patch("download_report.validate_url", return_value=(True, ""))
    @patch(
        "download_report.resolve_pdf_url_from_xueqiu_timeline",
        return_value=(True, "https://stockn.xueqiu.com/test.pdf", "OK"),
    )
    def test_xueqiu_resolve_flow(self, mock_resolve, mock_validate, mock_download):
        mock_download.return_value = (True, "OK", 50000)
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(SystemExit) as exc_info:
                main([
                    "--stock-code", "00700",
                    "--report-type", "年报",
                    "--year", "2024",
                    "--save-dir", tmpdir,
                    "--xueqiu-timeline-url",
                    "https://xueqiu.com/statuses/stock_timeline.json?symbol_id=00700",
                    "--cookie",
                    "xq_a_token=a; xq_r_token=b",
                ])
            assert exc_info.value.code == EXIT_SUCCESS
        mock_resolve.assert_called_once()
        assert mock_download.call_args[1]["url"] == "https://stockn.xueqiu.com/test.pdf"

    @patch("download_report.download_annual_report")
    @patch("download_report.validate_url", return_value=(True, ""))
    def test_network_failure_exit_code(self, mock_validate, mock_download):
        mock_download.return_value = (False, "Network error after 3 attempts", 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(SystemExit) as exc_info:
                main([
                    "--url", "https://stockn.xueqiu.com/test.pdf",
                    "--stock-code", "SH600887",
                    "--report-type", "年报",
                    "--year", "2024",
                    "--save-dir", tmpdir,
                ])
            assert exc_info.value.code == EXIT_NETWORK_FAILURE

    @patch("download_report.download_annual_report")
    @patch("download_report.validate_url", return_value=(True, ""))
    def test_validation_failure_exit_code(self, mock_validate, mock_download):
        mock_download.return_value = (False, "PDF validation failed: bad magic", 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(SystemExit) as exc_info:
                main([
                    "--url", "https://stockn.xueqiu.com/test.pdf",
                    "--stock-code", "SH600887",
                    "--report-type", "年报",
                    "--year", "2024",
                    "--save-dir", tmpdir,
                ])
            assert exc_info.value.code == EXIT_PDF_VALIDATION_FAILURE

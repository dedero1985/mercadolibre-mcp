"""Offline OAuth security tests: no real profiles, credentials, browser or network."""

from __future__ import annotations

import base64
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
import warnings
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

from mercadolibre_mcp import auth


class PKCEPrimitivesTests(unittest.TestCase):
    def test_rfc7636_s256_vector(self) -> None:
        self.assertEqual(
            auth._pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
            "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        )

    def test_verifiers_are_fresh_unreserved_and_have_required_entropy(self) -> None:
        with patch.object(auth.secrets, "token_urlsafe", wraps=auth.secrets.token_urlsafe) as random:
            verifiers = [auth._generate_pkce_verifier() for _ in range(32)]
        self.assertEqual(len(set(verifiers)), 32)
        for call in random.call_args_list:
            self.assertGreaterEqual(call.args[0], 32)
        for verifier in verifiers:
            self.assertRegex(verifier, r"^[A-Za-z0-9._~-]{43,128}$")
            self.assertEqual(len(base64.urlsafe_b64decode(verifier + "=")), 32)
            self.assertRegex(auth._pkce_challenge(verifier), r"^[A-Za-z0-9_-]{43}$")

    def test_verifier_validation_and_length_boundaries(self) -> None:
        for verifier in ("a" * 42, "a" * 129, "é" * 43, "=" * 43, "a" * 43 + "\n"):
            with self.subTest(length=len(verifier)), self.assertRaises(ValueError):
                auth._pkce_challenge(verifier)
        for verifier in ("a" * 43, ".~-_" * 32):
            self.assertEqual(len(auth._pkce_challenge(verifier)), 43)

    def test_mla_and_mlu_authorization_urls(self) -> None:
        for site, domain in (("MLA", "auth.mercadolibre.com.ar"), ("MLU", "auth.mercadolibre.com.uy")):
            with self.subTest(site=site):
                uri = "https://example.test/callback?tenant=a%2Fb"
                parsed = urlsplit(auth._build_authorization_url("fake-client", uri, site, "c", "s"))
                self.assertEqual((parsed.scheme, parsed.netloc, parsed.path), ("https", domain, "/authorization"))
                self.assertEqual(parse_qs(parsed.query), {
                    "response_type": ["code"], "client_id": ["fake-client"],
                    "redirect_uri": [uri], "code_challenge": ["c"],
                    "code_challenge_method": ["S256"], "state": ["s"],
                })


class BrowserLaunchTests(unittest.TestCase):
    def test_noisy_failing_launcher_cannot_expose_url_or_state(self) -> None:
        # A fake webbrowser module launches a noisy child with inherited file descriptors.
        # Exercise real OS output suppression, not just Python stream redirection.
        with tempfile.TemporaryDirectory() as directory:
            module = Path(directory) / "webbrowser.py"
            module.write_text(
                "import subprocess, sys\n"
                "def open(url):\n"
                "    subprocess.run([sys.executable, '-c', "
                "'import sys; print(sys.argv[1]); print(sys.argv[1], file=sys.stderr); "
                "sys.exit(1)', url], check=False)\n"
                "    return False\n",
                encoding="utf-8",
            )
            script = (
                "from mercadolibre_mcp.auth import _open_authorization_browser; "
                "print(_open_authorization_browser("
                "'https://example.test/authorize?state=synthetic-secret-state'))"
            )
            env = {**os.environ, "PYTHONPATH": directory}
            result = subprocess.run(
                [sys.executable, "-c", script], cwd=directory, env=env,
                capture_output=True, text=True, timeout=10, check=False,
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "False\n")
        self.assertEqual(result.stderr, "")

    def test_browser_url_uses_stdin_and_both_streams_are_suppressed(self) -> None:
        with patch.object(auth.subprocess, "run", return_value=Mock(returncode=0)) as run:
            self.assertTrue(auth._open_authorization_browser("synthetic-url"))
        self.assertNotIn("synthetic-url", run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs["input"], "synthetic-url")
        self.assertEqual(run.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.DEVNULL)

    def test_browser_process_errors_fail_closed(self) -> None:
        for error in (OSError("synthetic-url"), subprocess.TimeoutExpired("synthetic-url", 30)):
            with self.subTest(error=type(error).__name__):
                with patch.object(auth.subprocess, "run", side_effect=error):
                    self.assertFalse(auth._open_authorization_browser("synthetic-url"))


class OAuthFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(auth.os.environ, {}, clear=True))
        self.store_factory = self.stack.enter_context(patch.object(auth, "TokenStore"))
        self.store = self.store_factory.return_value
        self.store.has_token.return_value = False
        self.store.is_expired.return_value = True
        self.store.get_refresh_token.return_value = None
        self.store.get_user_id.return_value = 123
        self.store._path = "fake-profile-not-written"
        self.post = self.stack.enter_context(patch.object(auth.httpx, "post"))
        self.token_data = {"access_token": "fake-access", "refresh_token": "fake-refresh"}
        self.post.return_value.json.return_value = self.token_data
        self.browser = self.stack.enter_context(
            patch.object(auth, "_open_authorization_browser", return_value=True)
        )
        self.paste = self.stack.enter_context(patch.object(auth.getpass, "getpass"))
        self.visible_input = self.stack.enter_context(patch("builtins.input", side_effect=AssertionError("No visible input")))
        self.stdout = self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.stderr = self.stack.enter_context(redirect_stderr(io.StringIO()))
        self.redirect_uri = "https://example.test/callback?tenant=a%2Fb&tag=one&tag=two&empty="
        self.paste.side_effect = lambda *_: self.callback()

    def authorization_params(self) -> dict[str, list[str]]:
        return parse_qs(urlsplit(self.browser.call_args.args[0]).query)

    def callback(self, response_query: str | None = None) -> str:
        if response_query is None:
            response_query = urlencode({"code": "fake-code", "state": self.authorization_params()["state"][0]})
        return self.redirect_uri + "&" + response_query

    def authorize(self, *, interactive: bool = True) -> auth.TokenStore:
        return auth.ensure_token(
            "MLU", "fake-client", "fake-secret", self.redirect_uri, interactive=interactive,
        )

    def assert_no_interaction(self) -> None:
        self.browser.assert_not_called()
        self.paste.assert_not_called()
        self.visible_input.assert_not_called()

    def assert_rejected(self, callback: str) -> str:
        self.paste.side_effect = None
        self.paste.return_value = callback
        self.post.reset_mock()
        self.store.update.reset_mock()
        with self.assertRaises(RuntimeError) as error:
            self.authorize()
        self.post.assert_not_called()
        self.store.update.assert_not_called()
        return str(error.exception)

    def test_full_flow_binds_verifier_to_challenge_and_preserves_redirect(self) -> None:
        result = self.authorize()
        self.assertIs(result, self.store)
        self.post.assert_called_once()
        self.assertEqual(self.post.call_args.args, ("https://api.mercadolibre.com/oauth/token",))
        data = self.post.call_args.kwargs["data"]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(data["code_verifier"].encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        self.assertEqual(self.authorization_params()["code_challenge"], [challenge])
        self.assertEqual(data, {
            "grant_type": "authorization_code", "client_id": "fake-client",
            "client_secret": "fake-secret", "code": "fake-code",
            "redirect_uri": self.redirect_uri, "code_verifier": data["code_verifier"],
        })
        self.store.update.assert_called_once_with(self.token_data)
        self.visible_input.assert_not_called()
        output = self.stdout.getvalue() + self.stderr.getvalue()
        for secret in ("fake-secret", "fake-code", "fake-access", "fake-refresh", data["code_verifier"], challenge, self.authorization_params()["state"][0], self.redirect_uri, self.browser.call_args.args[0]):
            self.assertNotIn(secret, output)

    def test_each_interactive_attempt_has_fresh_verifier_and_state(self) -> None:
        with patch.object(auth.secrets, "token_urlsafe", wraps=auth.secrets.token_urlsafe) as random:
            self.authorize()
            first = self.authorization_params()
            verifier = self.post.call_args.kwargs["data"]["code_verifier"]
            self.authorize()
        second = self.authorization_params()
        self.assertEqual(random.call_count, 4)
        self.assertTrue(all(call.args[0] >= 32 for call in random.call_args_list))
        self.assertNotEqual(first["state"], second["state"])
        self.assertNotEqual(first["code_challenge"], second["code_challenge"])
        self.assertNotEqual(verifier, self.post.call_args.kwargs["data"]["code_verifier"])
        self.assertRegex(second["state"][0], r"^[A-Za-z0-9_-]{43,128}$")

    def test_missing_duplicate_blank_and_wrong_state_never_exchange(self) -> None:
        for query in ("code=fake-code", "code=fake-code&state=", "code=fake-code&state=%20", "code=fake-code&state=wrong", "code=fake-code&state=é", "code=fake-code&state=a&state=a", "code=fake-code&state=a&%73tate=b"):
            with self.subTest(query=query):
                self.assert_rejected(self.callback(query))

    def test_state_uses_constant_time_comparison(self) -> None:
        with patch.object(auth.secrets, "compare_digest", wraps=auth.secrets.compare_digest) as compare:
            self.authorize()
        state = self.authorization_params()["state"][0].encode("utf-8")
        compare.assert_called_once_with(state, state)

    def test_missing_duplicate_and_blank_code_never_exchange(self) -> None:
        for code_query in ("", "code=&", "code=%20&", "code=a&code=a&", "code=a&%63ode=b&"):
            with self.subTest(query=code_query):
                self.paste.side_effect = lambda *_, query=code_query: self.callback(query + urlencode({"state": self.authorization_params()["state"][0]}))
                with self.assertRaises(RuntimeError):
                    self.authorize()
                self.post.assert_not_called()

    def test_callback_target_and_static_query_validation(self) -> None:
        valid_query = "code=fake-code&state=expected"
        invalid_targets = (
            "http://example.test/callback?tenant=a%2Fb&tag=one&tag=two&empty=",
            "https://other.test/callback?tenant=a%2Fb&tag=one&tag=two&empty=",
            "https://example.test:443/callback?tenant=a%2Fb&tag=one&tag=two&empty=",
            "https://example.test/other?tenant=a%2Fb&tag=one&tag=two&empty=",
            "https://example.test/callback/?tenant=a%2Fb&tag=one&tag=two&empty=",
            "https://example.test/callback?tenant=changed&tag=one&tag=two&empty=",
            "https://example.test/callback?tag=one&tag=two&empty=",
            "https://example.test/callback?tenant=a%2Fb&tenant=a%2Fb&tag=one&tag=two&empty=",
            "https://example.test/callback?tenant=a%2Fb&tag=one&empty=",
        )
        with patch.object(auth.secrets, "token_urlsafe", return_value="expected"), patch.object(auth, "_generate_pkce_verifier", return_value="a" * 43):
            for target in invalid_targets:
                with self.subTest(target=target):
                    self.assert_rejected(target + "&" + valid_query)

    def test_static_query_order_and_equivalent_encoding_are_accepted(self) -> None:
        self.paste.side_effect = lambda *_: "https://example.test/callback?empty=&tag=two&tenant=a%2fb&tag=one&" + urlencode({"code": "fake-code", "state": self.authorization_params()["state"][0]})
        self.authorize()
        self.post.assert_called_once()
        self.assertEqual(self.post.call_args.kwargs["data"]["redirect_uri"], self.redirect_uri)

    def test_malformed_urls_and_fragments_never_exchange(self) -> None:
        for callback in (
            "", "/callback?code=a&state=b", "not a url", "https://[bad/callback",
            "https://example.test:invalid/callback?code=a&state=b",
            "https://example.test:99999/callback?code=a&state=b",
            "https://user@example.test/callback?code=a&state=b",
            "https://example.test\\@evil.test/callback?code=a&state=b",
            "\n" + self.callback("code=a&state=b"),
            self.callback("code=a&state=b\t"), self.callback("code=a&state=b#"),
            self.callback("code=a&state=b#fake-secret"),
            self.callback("code=%ZZ&state=b"), self.callback("code=%FF&state=b"),
            self.callback("code=a&state=b&malformed"),
        ):
            with self.subTest(callback=callback):
                self.assert_rejected(callback)

    def test_oauth_error_replies_are_redacted_and_never_exchanged(self) -> None:
        for error_query in ("error=untrusted-sensitive-error", "error=", "error_description=untrusted-sensitive-error", "error_uri=https%3A%2F%2Fevil.test%2Funtrusted-sensitive-error"):
            self.paste.side_effect = lambda *_, query=error_query: self.callback() + "&" + query
            with self.subTest(error=error_query), self.assertRaises(RuntimeError) as error:
                self.authorize()
            self.assertNotIn("untrusted-sensitive-error", str(error.exception))
            self.post.assert_not_called()
        self.assertNotIn("untrusted-sensitive-error", self.stdout.getvalue() + self.stderr.getvalue())

    def test_invalid_registered_redirect_is_rejected_before_browser(self) -> None:
        for uri in ("https://example.test/callback#", "https://example.test/callback?state=x", "https://example.test/callback?code=x", "https://example.test/callback?error=x", "bad-uri"):
            self.redirect_uri = uri
            with self.subTest(uri=uri), self.assertRaises(RuntimeError):
                self.authorize()
            self.assert_no_interaction()
            self.post.assert_not_called()

    def test_browser_failure_does_not_print_url_or_paste(self) -> None:
        for failure in (False, RuntimeError("secret-browser-url")):
            self.browser.return_value = False
            self.browser.side_effect = failure if isinstance(failure, Exception) else None
            with self.subTest(failure=type(failure).__name__), self.assertRaises(RuntimeError) as error:
                self.authorize()
            output = str(error.exception) + self.stdout.getvalue() + self.stderr.getvalue()
            self.assertIn("default browser", str(error.exception))
            for secret in ("secret-browser-url", self.browser.call_args.args[0], self.authorization_params()["state"][0]):
                self.assertNotIn(secret, output)
            self.paste.assert_not_called()
            self.post.assert_not_called()

    def test_hidden_input_refuses_warning_fallback_and_input_errors(self) -> None:
        def insecure_fallback(*_: object) -> str:
            warnings.warn("fake-sensitive-warning", auth.getpass.GetPassWarning)
            self.fail("Must stop before getpass can echo input")

        for failure in (insecure_fallback, EOFError("fake-sensitive-warning"), OSError("fake-sensitive-warning")):
            self.paste.side_effect = failure
            with self.subTest(failure=type(failure).__name__), self.assertRaises(RuntimeError) as error:
                self.authorize()
            self.assertIn("private terminal", str(error.exception))
            self.assertNotIn("fake-sensitive-warning", str(error.exception) + self.stderr.getvalue())
            self.post.assert_not_called()
            self.visible_input.assert_not_called()

    def test_token_exchange_errors_are_sanitized(self) -> None:
        for failure in (auth.httpx.RequestError("fake-sensitive-response"), ValueError("fake-sensitive-response")):
            self.post.side_effect = failure
            with self.subTest(failure=type(failure).__name__), self.assertRaises(RuntimeError) as error:
                self.authorize()
            self.assertNotIn("fake-sensitive-response", str(error.exception))
            self.assertTrue(error.exception.__suppress_context__)
            self.store.update.assert_not_called()

    def test_cached_valid_profile_is_noninteractive_and_needs_no_credentials(self) -> None:
        self.store.has_token.return_value = True
        self.store.is_expired.return_value = False
        for interactive in (True, False):
            self.assertIs(auth.ensure_token("MLU", interactive=interactive), self.store)
        self.assert_no_interaction()
        self.post.assert_not_called()

    def test_refresh_payload_and_noninteractive_behavior_are_unchanged(self) -> None:
        self.store.has_token.return_value = True
        self.store.get_refresh_token.return_value = "fake-refresh"
        for interactive in (True, False):
            self.assertIs(self.authorize(interactive=interactive), self.store)
            self.assertEqual(self.post.call_args.kwargs["data"], {
                "grant_type": "refresh_token", "client_id": "fake-client",
                "client_secret": "fake-secret", "refresh_token": "fake-refresh",
            })
        self.assert_no_interaction()

    def test_missing_token_noninteractive_has_actionable_error(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "--site-id MLU"):
            auth.ensure_token("MLU", interactive=False)
        self.assert_no_interaction()
        self.post.assert_not_called()

    def test_expired_without_refresh_clears_without_prompting(self) -> None:
        self.store.has_token.return_value = True
        with self.assertRaisesRegex(RuntimeError, "--site-id MLU"):
            self.authorize(interactive=False)
        self.store.clear.assert_called_once()
        self.assert_no_interaction()
        self.post.assert_not_called()

    def test_failed_refresh_does_not_start_noninteractive_authorization(self) -> None:
        self.store.has_token.return_value = True
        self.store.get_refresh_token.return_value = "fake-refresh"
        self.post.return_value.raise_for_status.side_effect = auth.httpx.HTTPStatusError(
            "fake-refresh-failure", request=Mock(), response=Mock(),
        )
        with self.assertRaisesRegex(RuntimeError, "--site-id MLU"):
            self.authorize(interactive=False)
        self.store.clear.assert_called_once()
        self.assert_no_interaction()

    def test_missing_credentials_does_not_open_browser(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "credentials not configured"):
            auth.ensure_token("MLU")
        self.assert_no_interaction()
        self.post.assert_not_called()

    def test_environment_configuration_keeps_exact_redirect(self) -> None:
        with patch.dict(auth.os.environ, {
            "MERCADOLIBRE_CLIENT_ID": "fake-env-client",
            "MERCADOLIBRE_CLIENT_SECRET": "fake-env-secret",
            "MERCADOLIBRE_REDIRECT_URI": self.redirect_uri,
        }, clear=True):
            auth.ensure_token("MLA")
        self.assertEqual(urlsplit(self.browser.call_args.args[0]).netloc, "auth.mercadolibre.com.ar")
        data = self.post.call_args.kwargs["data"]
        self.assertEqual(data["client_id"], "fake-env-client")
        self.assertEqual(data["client_secret"], "fake-env-secret")
        self.assertEqual(data["redirect_uri"], self.redirect_uri)


if __name__ == "__main__":
    unittest.main()

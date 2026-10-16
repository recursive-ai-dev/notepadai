"""Run with python3 -m unittest discover -s tests -v (Tk tests need a display)."""
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
import urllib.error
from unittest.mock import patch

import notepadai as n


class FilesAndSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "note.txt"

    def test_failed_atomic_save_keeps_original_and_cleans_temp(self):
        self.path.write_bytes(b"original")
        with patch.object(n.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                n.atomic_write(self.path, b"new")
        self.assertEqual(self.path.read_bytes(), b"original")
        self.assertEqual(list(Path(self.temp.name).iterdir()), [self.path])

    def test_atomic_save_preserves_permissions_and_symlink(self):
        self.path.write_bytes(b"old")
        self.path.chmod(0o640)
        link = self.path.with_name("linked.txt")
        link.symlink_to(self.path)
        n.atomic_write(link, b"new")
        self.assertTrue(link.is_symlink())
        self.assertEqual(self.path.read_bytes(), b"new")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)

    def test_settings_are_private_and_round_trip(self):
        with patch.object(n, "CONFIG_PATH", str(self.path)):
            n.save_config(dict(n.DEFAULTS, api_key="test-secret"))
            self.assertEqual(n.load_config()["api_key"], "test-secret")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_malformed_config_falls_back_and_reports_problem(self):
        for value in ("[]", "not json", '{"max_steps": -1}', '{"send_active_tab": "false"}'):
            with self.subTest(value=value):
                self.path.write_text(value)
                problems = []
                with patch.object(n, "CONFIG_PATH", str(self.path)):
                    self.assertEqual(n.load_config(problems.append), n.DEFAULTS)
                self.assertEqual(len(problems), 1)
                self.assertEqual(self.path.read_text(), value)

    def test_invalid_settings_are_rejected_without_mutation(self):
        invalid = {"temperature": [float("nan"), float("inf"), -1, 3, True],
                   "max_steps": [0, 21, 1.2, True], "model": ["", "  ", None],
                   "base_url": ["ftp://host", "https://", "https://a:bad", "https://a/?x=1",
                                "https://user:pass@host", "https://a/v1/chat/completions"]}
        for key, values in invalid.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    n.validate_config({key: value})
        source = {"model": " test ", "unknown": "ignored"}
        self.assertEqual(n.validate_config(source)["model"], "test")
        self.assertEqual(source["model"], " test ")

    def test_utf8_bom_and_crlf_are_recognized(self):
        raw = b"\xef\xbb\xbfhello\r\nworld\r\n"
        self.path.write_bytes(raw)
        self.assertEqual(n.read_document(self.path), ("hello\nworld\n", "\r\n", True, raw))

    def test_invalid_encoding_binary_and_oversized_files_are_rejected(self):
        for raw in (b"\xffbad", b"binary\x00file"):
            self.path.write_bytes(raw)
            with self.assertRaises(ValueError):
                n.read_document(self.path)
        self.path.write_bytes(b"12345")
        with patch.object(n, "MAX_FILE_BYTES", 4), self.assertRaises(ValueError):
            n.read_document(self.path)


class ProtocolTests(unittest.TestCase):
    def test_command_whitespace_is_preserved(self):
        commands, visible = n.parse_reply('Done. <append tab="2">\nnext\n</append>')
        self.assertEqual(commands, [("append", {"tab": "2"}, "\nnext\n")])
        self.assertEqual(visible, "Done.")

    def test_fenced_examples_are_displayed_without_execution(self):
        for fence in ("```", "~~~~"):
            reply = f'{fence}xml\n<write>example</write>\n{fence}\n<append>real</append>'
            commands, visible = n.parse_reply(reply)
            self.assertEqual(commands, [("append", {}, "real")])
            self.assertIn("<write>example</write>", visible)
        self.assertEqual(n.parse_reply("```\n<write>never</write>")[0], [])

    def test_code_fences_inside_written_text_are_preserved(self):
        body = "# Example\n```python\nprint('hello')\n```\n"
        self.assertEqual(n.parse_reply(f"<write>{body}</write>")[0], [("write", {}, body)])

    def test_malformed_batch_is_rejected_before_any_commands(self):
        for reply in ('<write/>', '<write>unfinished', '<write tab="1" tab="2">x</write>',
                      '<write typo="1">x</write>', '<write tab="abc">x</write>',
                      '<read>hidden</read>', '<replace><new>x</new></replace>',
                      '<append>valid</append><write>broken'):
            with self.subTest(reply=reply), self.assertRaises(ValueError):
                n.parse_reply(reply)

    def test_bad_provider_responses_are_actionable_errors(self):
        for data in ({}, {"choices": []}, {"choices": [{"message": {"content": None}}]},
                     {"choices": [{"message": {"content": "<write>partial"}, "finish_reason": "length"}]},
                     ["wrong shape"]):
            response = io.BytesIO(json.dumps(data).encode())
            with self.subTest(data=data), patch.object(n.urllib.request, "urlopen", return_value=response):
                with self.assertRaises(RuntimeError):
                    n.chat_completion(n.DEFAULTS, [])

    def test_request_shape_and_normal_response(self):
        response = io.BytesIO(b'{"choices":[{"message":{"content":"Hello"}}]}')
        with patch.object(n.urllib.request, "urlopen", return_value=response) as request:
            self.assertEqual(n.chat_completion(dict(n.DEFAULTS, api_key="test-key"),
                                              [{"role": "user", "content": "Hi"}]), "Hello")
        req = request.call_args.args[0]
        self.assertTrue(req.full_url.endswith("/v1/chat/completions"))
        self.assertEqual(req.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(json.loads(req.data)["messages"][0]["content"], "Hi")

    def test_provider_invalid_json_and_large_response(self):
        for response in (b"<html>not an API", b"x" * 21):
            with patch.object(n, "MAX_RESPONSE_BYTES", 20), patch.object(
                    n.urllib.request, "urlopen", return_value=io.BytesIO(response)):
                with self.assertRaises(RuntimeError):
                    n.chat_completion(n.DEFAULTS, [])

    def test_http_errors_redact_key(self):
        error = urllib.error.HTTPError("https://example.test", 401, "Unauthorized", {},
                                       io.BytesIO(b"Bad key: secret-token"))
        with patch.object(n.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, r"HTTP 401: Bad key: \[redacted\]"):
                n.chat_completion(dict(n.DEFAULTS, api_key="secret-token"), [])


class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            root = tk.Tk()
            root.withdraw()
            root.destroy()
        except tk.TclError as error:
            raise unittest.SkipTest(f"Tk display unavailable: {error}")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "note.txt"
        self.root = tk.Tk()
        self.root.withdraw()
        with patch.object(n, "load_config", return_value=dict(n.DEFAULTS)):
            self.app = n.App(self.root, [])
        self.root.update()
        self.addCleanup(self.destroy)
        # No regression test may accidentally send text to an external provider or open a modal dialog.
        self.network = patch.object(n, "chat_completion", side_effect=AssertionError("unexpected network"))
        self.network.start()
        self.addCleanup(self.network.stop)
        for name in ("showerror", "askyesno", "askyesnocancel"):
            mock = patch.object(n.messagebox, name, side_effect=AssertionError(f"unexpected {name}"))
            mock.start()
            self.addCleanup(mock.stop)

    def destroy(self):
        try:
            if self.root.winfo_exists():
                self.app.stop(log=False)
                self.root.after_cancel(self.app.poll_id)
                self.root.destroy()
        except tk.TclError:
            pass

    def run_snapshot(self):
        return n.AgentRun(dict(self.app.cfg), [], "", self.app.current().id,
                          {tab.id: n.fingerprint(tab.content.encode()) for tab in self.app.tabs.values()})

    def open_note(self, raw=b"original\n"):
        self.path.write_bytes(raw)
        return self.app.open_file(str(self.path))

    def pump_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertTrue(predicate(), "UI operation timed out")

    def test_dirty_marker_and_undo_back_to_saved_state(self):
        tab = self.open_note()
        tab.text.insert("end-1c", "edit")
        self.root.update()
        self.assertTrue(tab.dirty)
        self.assertIn("•", self.app.nb.tab(tab.frame, "text"))
        tab.text.edit_undo()
        self.root.update()
        self.assertFalse(tab.dirty)
        self.assertNotIn("•", self.app.nb.tab(tab.frame, "text"))

    def test_open_deduplicates_paths_and_reuses_empty_tab(self):
        first = self.open_note()
        again = self.app.open_file(str(self.path.parent / "." / self.path.name))
        self.assertIs(first, again)
        self.assertEqual(len(self.app.tabs), 1)

    def test_ai_target_is_pinned_when_user_switches_tabs(self):
        target = self.open_note()
        run = self.run_snapshot()
        other = self.app.new_tab("Other", "untouched")
        self.assertEqual(self.app.run_command("write", {}, "changed", run), "ok")
        self.assertEqual(target.content, "changed")
        self.assertEqual(other.content, "untouched")

    def test_concurrent_user_edit_is_protected_until_read(self):
        tab = self.open_note()
        run = self.run_snapshot()
        tab.text.insert("end-1c", "user edit")
        self.assertIn("changed since", self.app.run_command("write", {}, "wrong", run))
        self.assertEqual(tab.content, "original\nuser edit")
        self.app.run_command("read", {}, "", run)
        self.assertEqual(self.app.run_command("write", {}, "reviewed", run), "ok")

    def test_closed_request_target_never_falls_back_to_another_tab(self):
        target = self.open_note()
        run = self.run_snapshot()
        self.app.close_tab(target)
        self.assertIn("no such tab", self.app.run_command("write", {}, "wrong", run))
        self.assertEqual(self.app.current().content, "")

    def test_ai_rewrite_is_one_undo_and_does_not_merge_with_typing(self):
        tab = self.open_note()
        tab.text.insert("end-1c", "typed")
        run = self.run_snapshot()
        self.app.run_command("write", {}, "rewritten\n", run)
        tab.text.edit_undo()
        self.assertEqual(tab.content, "original\ntyped")
        tab.text.edit_undo()
        self.assertEqual(tab.content, "original\n")
        tab.text.edit_redo()
        self.assertEqual(tab.content, "original\ntyped")

    def test_append_and_unicode_replace_preserve_exact_whitespace(self):
        tab = self.open_note("😀 café\nkeep\n".encode())
        run = self.run_snapshot()
        self.app.run_command("replace", {}, "<old>café\n</old><new>tea\n\n</new>", run)
        self.app.run_command("append", {}, "\nmore\n", run)
        self.assertEqual(tab.content, "😀 tea\n\nkeep\n\nmore\n")

    def test_ai_cannot_close_dirty_note(self):
        tab = self.app.current()
        tab.text.insert("1.0", "keep me")
        self.assertIn("unsaved", self.app.run_command("close_tab", {}, "", self.run_snapshot()))
        self.assertIn(tab.id, self.app.tabs)

    def test_failed_command_skips_remainder(self):
        tab = self.open_note()
        box = {}
        self.app.handle_reply('<replace><old>missing</old><new>x</new></replace><write>bad</write>',
                              box, self.run_snapshot())
        self.assertEqual(tab.content, "original\n")
        self.assertIn("skipped", box["feedback"])

    def test_cancelled_close_and_cancelled_save_keep_note(self):
        tab = self.app.current()
        tab.text.insert("1.0", "keep")
        with patch.object(n.messagebox, "askyesnocancel", return_value=None):
            self.assertFalse(self.app.close_tab(tab))
        with patch.object(n.messagebox, "askyesnocancel", return_value=True), patch.object(
                n.filedialog, "asksaveasfilename", return_value=""):
            self.assertFalse(self.app.close_tab(tab))
        self.assertEqual(tab.content, "keep")

    def test_save_and_close_writes_selected_dirty_tab(self):
        tab = self.app.current()
        tab.text.insert("1.0", "keep on disk")
        self.app.new_tab(content="different active note")
        with patch.object(n.messagebox, "askyesnocancel", return_value=True), patch.object(
                n.filedialog, "asksaveasfilename", return_value=str(self.path)):
            self.assertTrue(self.app.close_tab(tab))
        self.assertEqual(self.path.read_text(), "keep on disk")
        self.assertEqual(self.app.current().content, "different active note")

    def test_failed_settings_save_does_not_change_live_configuration(self):
        self.app.open_settings()
        dialog = self.app.settings_window
        dialog.vars["model"].set("changed")
        with patch.object(n, "save_config", side_effect=OSError("read-only")), patch.object(
                n.messagebox, "showerror"):
            dialog.save()
        self.assertEqual(self.app.cfg["model"], n.DEFAULTS["model"])
        self.assertTrue(dialog.winfo_exists())
        with patch.object(n, "save_config") as save:
            dialog.save()
        save.assert_called_once()
        self.assertEqual(self.app.cfg["model"], "changed")

    def test_send_and_stop_stay_visible_at_minimum_window_size(self):
        self.root.geometry("760x460")
        self.root.deiconify()
        self.pump_until(lambda: self.root.winfo_width() == 760 and self.app.send_button.winfo_ismapped())
        self.root.update()
        for widget in (self.app.entry, self.app.send_button, self.app.stop_button):
            self.assertTrue(widget.winfo_ismapped())
            self.assertLessEqual(widget.winfo_x() + widget.winfo_width(), widget.master.winfo_width())
            self.assertLessEqual(widget.winfo_y() + widget.winfo_height(), widget.master.winfo_height())

    def test_save_failure_keeps_dirty_note_and_original_file(self):
        tab = self.open_note()
        tab.text.insert("end-1c", "unsaved")
        with patch.object(n, "atomic_write", side_effect=OSError("disk full")), patch.object(
                n.messagebox, "showerror") as error:
            self.assertFalse(self.app.save())
        error.assert_called_once()
        self.assertTrue(tab.dirty)
        self.assertEqual(self.path.read_bytes(), b"original\n")

    def test_external_changes_require_confirmation(self):
        tab = self.open_note()
        tab.text.insert("end-1c", "mine")
        self.path.write_text("external")
        with patch.object(n.messagebox, "askyesno", return_value=False):
            self.assertFalse(self.app.save())
        self.assertEqual(self.path.read_text(), "external")
        with patch.object(n.messagebox, "askyesno", return_value=True):
            self.assertTrue(self.app.save())
        self.assertEqual(self.path.read_text(), "original\nmine")
        self.assertFalse(tab.dirty)

    def test_crlf_bom_and_final_newline_survive_save(self):
        tab = self.open_note(b"\xef\xbb\xbfhello\r\n")
        tab.text.insert("end-1c", "world\n")
        self.assertTrue(self.app.save())
        self.assertEqual(self.path.read_bytes(), b"\xef\xbb\xbfhello\r\nworld\r\n")

    def test_unchanged_mixed_line_endings_are_saved_byte_for_byte(self):
        raw = b"first\r\nsecond\nthird\r"
        self.open_note(raw)
        self.assertTrue(self.app.save())
        self.assertEqual(self.path.read_bytes(), raw)

    def test_save_as_cannot_overwrite_another_open_tab(self):
        self.open_note()
        tab = self.app.new_tab(content="different")
        with patch.object(n.filedialog, "asksaveasfilename", return_value=str(self.path)), patch.object(
                n.messagebox, "showerror"):
            self.assertFalse(self.app.save())
        self.assertIsNone(tab.path)
        self.assertEqual(self.path.read_bytes(), b"original\n")

    def test_cancelled_quit_keeps_all_tabs(self):
        first = self.app.current()
        first.text.insert("1.0", "one")
        self.app.new_tab(content="two")
        with patch.object(n.messagebox, "askyesnocancel", side_effect=[False, None]):
            self.app.quit()
        self.assertEqual(len(self.app.tabs), 2)
        self.assertFalse(self.app.closing)
        self.assertEqual(first.content, "one")

    def test_find_wraps_and_handles_unicode(self):
        tab = self.open_note("😀 Hello\nhello".encode())
        self.app.open_find()
        self.app.find_query.set("HELLO")
        tab.text.mark_set("insert", "end-1c")
        self.app.find_next()
        self.assertEqual(tab.text.get("sel.first", "sel.last"), "Hello")
        self.app.find_next()
        self.assertEqual(tab.text.get("sel.first", "sel.last"), "hello")

    def test_late_reply_after_stop_cannot_edit_or_restore_history(self):
        tab = self.open_note()
        run = self.run_snapshot()
        self.app.active_run = run
        self.app.clear_chat()
        done = threading.Event()
        self.app.q.put(("reply", run, "<write>bad</write>", [{"role": "assistant", "content": "bad"}], {}, done))
        self.pump_until(done.is_set)
        self.assertEqual(tab.content, "original\n")
        self.assertEqual(self.app.history, [])

    def test_background_read_edit_cycle_finishes_on_ui_thread(self):
        tab = self.open_note()
        seen = []
        def provider(cfg, messages):
            seen.append(messages)
            self.assertIsNot(threading.current_thread(), threading.main_thread())
            return '<read/>' if len(seen) == 1 else '<write>completed\n</write>Done.'
        with patch.object(n, "chat_completion", side_effect=provider):
            self.app.entry.insert("1.0", "edit this")
            self.app.send()
            self.pump_until(lambda: not self.app.busy)
        self.assertEqual(len(seen), 2)
        self.assertIn("original", seen[1][-1]["content"])
        self.assertEqual(tab.content, "completed\n")
        self.assertIsNone(self.app.active_run)

    def test_inflight_run_uses_settings_snapshot_and_stop_is_immediate(self):
        started, release = threading.Event(), threading.Event()
        seen = []
        def provider(cfg, messages):
            started.set()
            release.wait(2)
            seen.append(cfg["model"])
            return "<write>late</write>"
        with patch.object(n, "chat_completion", side_effect=provider):
            self.app.entry.insert("1.0", "hello")
            self.app.send()
            self.assertTrue(started.wait(1))
            self.app.cfg["model"] = "new-model"
            self.app.stop()
            self.assertFalse(self.app.busy)
            release.set()
            self.pump_until(lambda: bool(seen))
        self.assertEqual(seen, [n.DEFAULTS["model"]])
        self.assertEqual(self.app.current().content, "")

    def test_step_limit_and_network_failure_restore_controls(self):
        self.app.cfg["max_steps"] = 1
        with patch.object(n, "chat_completion", return_value="<list_tabs/>"):
            self.app.entry.insert("1.0", "list")
            self.app.send()
            self.pump_until(lambda: not self.app.busy)
        self.assertIn("step limit", self.app.chat.get("1.0", "end"))
        with patch.object(n, "chat_completion", side_effect=RuntimeError("offline")):
            self.app.entry.insert("1.0", "again")
            self.app.send()
            self.pump_until(lambda: not self.app.busy)
        self.assertEqual(str(self.app.send_button.cget("state")), "normal")
        self.assertIn("offline", self.app.chat.get("1.0", "end"))


if __name__ == "__main__":
    unittest.main()

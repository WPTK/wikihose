"""Every input the user can give wikihose must have a test that gives it.

The inventory is collected from the code: every key binding on the app and on each screen and widget in ``wikihose.ui``,
the footer label of each key in each mode, the help line of each key, and every ``@click=`` action. ``INVENTORY`` maps
each to the name of a test that exercises it. The test fails when an input has no entry, when an entry names a test that
does not exist, and when an entry no longer matches any input. An entry whose test name begins with ``manual:`` is a
check made by hand (it is not looked up). To add a key, declare the Binding, then add its entries here, then write the
test.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
import re
from pathlib import Path

from textual.binding import Binding

import wikihose.ui
from wikihose.ui.app import WikihoseApp
from wikihose.ui.bindings import declared_bindings, key_label
from wikihose.ui.dialogs import FIXED_HELP
from wikihose.ui.tabs.live import LiveTab

ROOT = Path(__file__).resolve().parents[2]

INVENTORY: dict[str, str] = {
    # Key bindings
    "binding:WikihoseApp:1:tab('live')": "test_number_keys_switch_tabs",
    "binding:WikihoseApp:2:tab('reading')": "test_number_keys_switch_tabs",
    "binding:WikihoseApp:3:tab('watchlist')": "test_number_keys_switch_tabs",
    "binding:WikihoseApp:4:tab('diff')": "test_number_keys_switch_tabs",
    "binding:WikihoseApp:5:tab('convergence')": "test_number_keys_switch_tabs",
    "binding:WikihoseApp:w:watch": "test_w_on_a_log_row_adds_that_article_and_badges_the_tab",
    "binding:WikihoseApp:question_mark:help": "test_help_lists_the_active_keys_in_live_mode",
    "binding:WikihoseApp:q:quit": "test_q_quits_and_closes_the_source",
    "binding:WikihoseApp:space:replay_pause": "test_replay_keys_pause_speed_and_skip",
    "binding:WikihoseApp:plus:replay_faster": "test_replay_keys_pause_speed_and_skip",
    "binding:WikihoseApp:minus:replay_slower": "test_replay_keys_pause_speed_and_skip",
    "binding:WikihoseApp:right_square_bracket:replay_skip": "test_replay_keys_pause_speed_and_skip",
    "binding:LiveTab:b:toggle_bots": "test_b_hides_and_shows_bot_edits",
    "binding:LiveTab:a:toggle_articles": "test_a_toggle_refills_the_log_from_the_last_edits",
    "binding:LiveTab:m:cycle_size": "test_size_key_cycles_through_the_steps",
    "binding:LiveTab:i:change_wiki": "test_i_switches_wiki_and_starts_the_numbers_over",
    "binding:LiveTab:p:toggle_pause": "test_pause_holds_the_log_while_the_numbers_keep_counting",
    "binding:HelpScreen:escape:dismiss": "test_help_closes",
    "binding:HelpScreen:question_mark:dismiss": "test_help_closes",
    "binding:AddPage:escape:dismiss(None)": "test_the_dialog_cancels_with_escape_and_with_the_button",
    "binding:WikiPrompt:escape:dismiss(None)": "test_the_wiki_dialog_cancels_with_escape",
    "binding:EditDetail:escape:dismiss": "test_edit_detail_is_not_a_help_screen_and_closes_with_escape",
    # Footer labels, per mode
    "footer:live:Bots": "test_clicking_each_live_footer_key_does_what_its_key_does",
    "footer:live:Articles": "test_clicking_each_live_footer_key_does_what_its_key_does",
    "footer:live:Size": "test_clicking_each_live_footer_key_does_what_its_key_does",
    "footer:live:Wiki": "test_clicking_each_live_footer_key_does_what_its_key_does",
    "footer:live:Pause": "test_clicking_each_live_footer_key_does_what_its_key_does",
    "footer:live:Watch": "test_clicking_each_live_footer_key_does_what_its_key_does",
    "footer:live:Help": "test_clicking_each_live_footer_key_does_what_its_key_does",
    "footer:live:Quit": "test_clicking_each_live_footer_key_does_what_its_key_does",
    "footer:replay:Play": "test_clicking_each_replay_footer_key_does_what_its_key_does",
    "footer:replay:Faster": "test_clicking_each_replay_footer_key_does_what_its_key_does",
    "footer:replay:Slower": "test_clicking_each_replay_footer_key_does_what_its_key_does",
    "footer:replay:Skip": "test_clicking_each_replay_footer_key_does_what_its_key_does",
    # Help lines
    "help:b": "test_help_lists_the_active_keys_in_live_mode",
    "help:a": "test_help_lists_the_active_keys_in_live_mode",
    "help:m": "test_help_lists_the_active_keys_in_live_mode",
    "help:i": "test_help_lists_the_active_keys_in_live_mode",
    "help:p": "test_help_lists_the_active_keys_in_live_mode",
    "help:w": "test_help_lists_the_active_keys_in_live_mode",
    "help:?": "test_help_lists_the_active_keys_in_live_mode",
    "help:q": "test_help_lists_the_active_keys_in_live_mode",
    "help:space": "test_help_lists_the_replay_keys_only_in_a_replay",
    "help:+": "test_help_lists_the_replay_keys_only_in_a_replay",
    "help:-": "test_help_lists_the_replay_keys_only_in_a_replay",
    "help:]": "test_help_lists_the_replay_keys_only_in_a_replay",
    "help:1-5": "test_help_lists_the_active_keys_in_live_mode",
    "help:Enter": "test_enter_on_a_log_row_opens_the_detail_with_the_article_link",
    "help:Tab": "test_tab_moves_the_focus_between_the_log_and_the_top_table",
    "help:Mouse": "test_clicking_a_log_row_opens_its_detail",
    # Mouse actions
    "click:pick": "test_clicking_the_filter_words_does_what_the_keys_do",
}


def _own_bindings(cls: type) -> list[Binding]:
    own = cls.__dict__.get("BINDINGS")
    return Binding.make_bindings(own) if own else []


def _ui_classes() -> list[type]:
    found: dict[str, type] = {}
    for info in pkgutil.walk_packages(wikihose.ui.__path__, "wikihose.ui."):
        module = importlib.import_module(info.name)
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__.startswith("wikihose.ui"):
                found[f"{cls.__module__}.{cls.__qualname__}"] = cls
    return list(found.values())


def collected() -> dict[str, list[str]]:
    """Every input, with the words a test for it must contain (a key name, a label, or a click word)."""
    found: dict[str, list[str]] = {}
    for cls in _ui_classes():
        for binding in _own_bindings(cls):
            found[f"binding:{cls.__name__}:{binding.key}:{binding.action}"] = _key_words(binding)
    for cls in (WikihoseApp, LiveTab):
        for binding in declared_bindings(cls):
            if binding.show:
                mode = "replay" if binding.action.startswith("replay_") else "live"
                found[f"footer:{mode}:{binding.description}"] = [binding.description]
            if not binding.action.startswith("tab("):
                found[f"help:{key_label(binding)}"] = [*_key_words(binding), binding.tooltip or binding.description]
    for key, text in FIXED_HELP:
        found[f"help:{key}"] = ["click"] if key == "Mouse" else [key.lower(), text]
    for path in (ROOT / "src" / "wikihose").rglob("*.py"):
        for action in re.findall(r"@click=(\w+)", path.read_text(encoding="utf-8")):
            found[f"click:{action}"] = ["click"]
    return found


def _key_words(binding: Binding) -> list[str]:
    """What a test calls the key: Textual's key name, the character shown for it, or the action it runs."""
    return [binding.key, binding.key_display or binding.key, binding.action.split("(")[0]]


def source_of_tests() -> dict[str, str]:
    """The source text of every test function under tests/, by name."""
    found: dict[str, str] = {}
    for path in (ROOT / "tests").rglob("test_*.py"):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test_"):
                first = min([node.lineno, *(d.lineno for d in node.decorator_list)])  # the decorators name the cases
                found[node.name] = found.get(node.name, "") + " ".join(lines[first - 1 : node.end_lineno])
    return found


def test_every_input_has_an_entry() -> None:
    missing = sorted(collected().keys() - INVENTORY.keys())
    assert not missing, f"inputs with no test in INVENTORY: {missing}"


def test_no_entry_is_stale() -> None:
    found = collected()
    stale = sorted(key for key in INVENTORY if key not in found)
    assert not stale, f"INVENTORY entries that match no input: {stale}"


def test_every_entry_names_a_real_test() -> None:
    tests = source_of_tests()
    bad = sorted(
        f"{key} -> {name}" for key, name in INVENTORY.items() if not name.startswith("manual:") and name not in tests
    )
    assert not bad, f"INVENTORY entries naming tests that do not exist: {bad}"


def test_every_named_test_mentions_its_input() -> None:
    """A test named for a key must contain that key (or its label, or its help text), so a rename cannot hollow it out."""
    tests = source_of_tests()
    found = collected()
    hollow = []
    for key, name in INVENTORY.items():
        if name.startswith("manual:") or key not in found or name not in tests:
            continue
        if not any(word in tests[name] for word in found[key]):
            hollow.append(f"{key} -> {name} (looked for any of {found[key]})")
    assert not hollow, "tests that never mention the input they are listed for: " + "; ".join(sorted(hollow))

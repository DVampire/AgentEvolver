"""Prompt: parsing the .html format, expanding module slots, and rendering.

Every agent's behaviour comes out of this path, and its failures are quiet ones:
a mis-parsed section produces an empty system prompt rather than an error, an
unexpanded ``<module>`` slot sends a literal tag to the model, and a cached
system message keeps serving stale text after an evolution step rewrites it.
Each of those is pinned down here.
"""

import pytest

from agentevolver.prompt.types import (
    Prompt,
    PromptConfig,
    PromptContext,
    _render_template,
    expand_modules,
    parse_prompt_file,
    parse_prompt_text,
)


def a_prompt_file(tmp_path, body, name="reason_act_agent"):
    path = tmp_path / f"{name}.html"
    path.write_text(body, encoding="utf-8")
    return str(path)


FULL = """
<html>
<head>
  <meta name="name" content="reason_act_agent">
  <meta name="description" content="Reason then act">
  <meta name="version" content="2.1.0">
  <meta name="enable_evolving" content="true">
</head>
<body>
  <div class="system">You may take {{ max_actions }} actions.</div>
  <div class="user">Task: {{ task }}</div>
</body>
</html>
"""


# -------------------------------------------------------------------- parsing
def test_the_metadata_block_becomes_the_config():
    config = parse_prompt_text(FULL)
    assert config.name == "reason_act_agent"
    assert config.description == "Reason then act"
    assert config.version == "2.1.0"
    assert config.enable_evolving is True


def test_both_sections_are_captured_separately():
    config = parse_prompt_text(FULL)
    assert "{{ max_actions }}" in config.system_template
    assert "{{ task }}" in config.user_template
    assert "max_actions" not in config.user_template


def test_evolving_is_off_unless_explicitly_declared():
    """A prompt that is a trainable variable by accident would be rewritten."""
    assert parse_prompt_text("<html><head></head><body></body></html>").enable_evolving is False
    assert parse_prompt_text(
        '<meta name="enable_evolving" content="TRUE">'
    ).enable_evolving is True


def test_a_missing_version_falls_back_to_a_first_version():
    assert parse_prompt_text("<html></html>").version == "1.0.0"


def test_nested_markup_inside_a_section_is_preserved():
    """The section body is inner HTML — the browser preview styles it."""
    config = parse_prompt_text(
        '<div class="system">a<div class="rule"><b>bold</b></div>z</div>'
    )
    assert config.system_template == 'a<div class="rule"><b>bold</b></div>z'


def test_content_outside_a_known_section_is_ignored():
    config = parse_prompt_text('<div class="notes">ignored</div><div class="system">kept</div>')
    assert config.system_template == "kept"
    assert "ignored" not in config.system_template


def test_html_entities_reach_the_model_decoded():
    """The template is prose for a model, not markup for a browser — it should
    read ``a & b``, not ``a &amp; b``."""
    config = parse_prompt_text('<div class="system">a &amp; b &#65;</div>')
    assert config.system_template == "a & b A"


def test_an_absent_section_is_empty_rather_than_missing():
    config = parse_prompt_text('<div class="system">only system</div>')
    assert config.user_template == ""


def test_a_file_is_read_and_parsed(tmp_path):
    config = parse_prompt_file(a_prompt_file(tmp_path, FULL))
    assert config.name == "reason_act_agent"


# ------------------------------------------------------------ module slots
def test_a_module_slot_is_replaced_by_the_module_body(tmp_path):
    (tmp_path / "rules.html").write_text(
        "<html><head><link rel='stylesheet' href='x.css'></head>"
        "<body><p>Batch your actions.</p></body></html>",
        encoding="utf-8",
    )
    expanded = expand_modules('<module src="rules.html"></module>', str(tmp_path))
    assert expanded == "<p>Batch your actions.</p>"


def test_a_module_s_own_head_never_reaches_the_model(tmp_path):
    """The module's CSS/JS is for the browser preview only."""
    (tmp_path / "m.html").write_text(
        "<html><head><style>p{color:red}</style></head><body>text</body></html>",
        encoding="utf-8",
    )
    assert "color:red" not in expand_modules('<module src="m.html"></module>', str(tmp_path))


def test_a_module_file_without_a_body_is_used_whole(tmp_path):
    (tmp_path / "m.html").write_text("bare fragment", encoding="utf-8")
    assert expand_modules('<module src="m.html"></module>', str(tmp_path)) == "bare fragment"


def test_a_nested_module_resolves_against_its_own_directory(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "inner.html").write_text("<body>inner</body>", encoding="utf-8")
    (tmp_path / "sub" / "outer.html").write_text(
        '<body>[<module src="inner.html"></module>]</body>', encoding="utf-8",
    )
    assert expand_modules('<module src="sub/outer.html"></module>', str(tmp_path)) == "[inner]"


def test_several_slots_are_all_expanded(tmp_path):
    for name in ("a", "b"):
        (tmp_path / f"{name}.html").write_text(f"<body>{name}</body>", encoding="utf-8")
    expanded = expand_modules(
        '<module src="a.html"></module>|<module src="b.html"></module>', str(tmp_path),
    )
    assert expanded == "a|b"


def test_attribute_order_does_not_matter(tmp_path):
    (tmp_path / "m.html").write_text("<body>ok</body>", encoding="utf-8")
    assert expand_modules('<module id="x" src="m.html" data-y="1"></module>', str(tmp_path)) == "ok"


def test_a_cyclic_module_is_refused_rather_than_recursing_forever(tmp_path):
    (tmp_path / "loop.html").write_text(
        '<body><module src="loop.html"></module></body>', encoding="utf-8",
    )
    with pytest.raises(ValueError, match="max depth"):
        expand_modules('<module src="loop.html"></module>', str(tmp_path))


def test_slots_are_expanded_when_a_prompt_file_is_read(tmp_path):
    """An unexpanded slot would send a literal ``<module>`` tag to the model."""
    (tmp_path / "rules.html").write_text("<body>BATCH</body>", encoding="utf-8")
    path = a_prompt_file(tmp_path, '<div class="system"><module src="rules.html"></module></div>')
    config = parse_prompt_file(path)
    assert config.system_template == "BATCH"
    assert "<module" not in config.system_template


def test_text_with_no_slots_passes_through_untouched(tmp_path):
    assert expand_modules("plain text", str(tmp_path)) == "plain text"


# ------------------------------------------------------------------ render
@pytest.mark.asyncio
async def test_the_system_message_renders_its_variables():
    prompt = Prompt(name="p", system_template="You may take {{ max_actions }} actions.")
    message = await prompt.get_system_message({"max_actions": 10})
    assert message.text == "You may take 10 actions."
    assert message.role == "system"


@pytest.mark.asyncio
async def test_the_user_message_is_a_text_content_part():
    prompt = Prompt(name="p", user_template="Task: {{ task }}")
    message = await prompt.get_user_message({"task": "fix it"})
    assert message.role == "user"
    assert message.text == "Task: fix it"


@pytest.mark.asyncio
async def test_static_defaults_fill_in_what_the_caller_omits():
    prompt = Prompt(name="p", system_template="{{ a }}/{{ b }}", variables={"a": "1", "b": "2"})
    assert (await prompt.get_system_message({"b": "override"})).text == "1/override"


@pytest.mark.asyncio
async def test_rendering_does_not_mutate_the_static_defaults():
    prompt = Prompt(name="p", system_template="{{ a }}", variables={"a": "1"})
    await prompt.get_system_message({"a": "2"})
    assert prompt.variables == {"a": "1"}


@pytest.mark.asyncio
async def test_the_system_message_is_cached_between_calls():
    """Assembled once per agent and reused every step — re-rendering is waste."""
    prompt = Prompt(name="p", system_template="{{ a }}")
    first = await prompt.get_system_message({"a": "1"})
    assert (await prompt.get_system_message({"a": "2"})) is first


@pytest.mark.asyncio
async def test_a_reload_defeats_the_cache_after_an_evolution_step():
    prompt = Prompt(name="p", system_template="{{ a }}")
    await prompt.get_system_message({"a": "1"})
    assert (await prompt.get_system_message({"a": "2"}, reload=True)).text == "2"


@pytest.mark.asyncio
async def test_the_user_message_is_re_rendered_every_turn():
    """It carries the turn's task; caching it would repeat the first one forever."""
    prompt = Prompt(name="p", user_template="{{ task }}")
    assert (await prompt.get_user_message({"task": "a"})).text == "a"
    assert (await prompt.get_user_message({"task": "b"})).text == "b"


def test_an_unfilled_variable_renders_empty_rather_than_leaving_the_marker():
    assert _render_template("[{{ missing }}]", {}) == "[]"


def test_a_template_with_no_variables_is_unchanged():
    assert _render_template("no variables here", {"unused": 1}) == "no variables here"


# ------------------------------------------------------------------ config
def test_a_config_converts_to_a_renderable_prompt():
    prompt = PromptConfig(name="p", system_template="s", variables={"a": 1}).to_prompt()
    assert isinstance(prompt, Prompt)
    assert prompt.system_template == "s"
    assert prompt.variables == {"a": 1}


def test_a_config_round_trips_through_its_serialised_form():
    original = PromptConfig(name="p", version="2.0.0", enable_evolving=True,
                            system_template="s", user_template="u", metadata={"k": "v"})
    restored = PromptConfig.model_validate(original.model_dump())
    assert restored.model_dump() == original.model_dump()


def test_deserialising_a_partial_record_fills_the_defaults():
    config = PromptConfig.model_validate({"name": "p"})
    assert config.version == "1.0.0"
    assert config.enable_evolving is False
    assert config.permission_mode == "workspace_write"
    assert config.variables == {}


def test_null_collections_serialise_as_empty_ones():
    """A ``None`` here would break every consumer that iterates them."""
    dumped = PromptConfig(name="p", variables=None, metadata=None).model_dump()
    assert dumped["variables"] == {} and dumped["metadata"] == {}


def test_a_prompt_context_needs_nothing_to_be_constructed():
    assert PromptContext().input == {}
